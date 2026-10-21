#!/usr/bin/env python3
"""Export a TrOCR checkpoint to ONNX for the handwrite-input runtime.

This is a one-off tool: it needs torch + transformers, while the daemon then
runs on onnxruntime + tokenizers only.  The export produces:

    encoder_model.onnx   pixel_values [B,3,384,384] -> last_hidden_state [B,577,384]
    decoder_model.onnx   input_ids [B,T] + encoder_hidden_states [B,577,384]
                         -> logits [B,1,vocab] (last position only)
    tokenizer.json       the exact tokenizers backend of the checkpoint
    onnx_config.json     special ids / preprocessing constants

The decoder is exported without a KV cache, which is what the checkpoint's
generation config uses anyway (use_cache=false) and is fast enough for
single-line recognition at this model size.

Usage:
    .venv/bin/python tools/export_trocr_onnx.py \
        [--model microsoft/trocr-small-handwritten] [--output DIR]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import ViTImageProcessor, VisionEncoderDecoderModel, XLMRobertaTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import handwrite_ocr  # noqa: E402


class Encoder(torch.nn.Module):
    def __init__(self, encoder):
        super().__init__()
        self.encoder = encoder

    def forward(self, pixel_values):
        return self.encoder(pixel_values=pixel_values).last_hidden_state


class Decoder(torch.nn.Module):
    """Run the causal decoder over the whole prefix, return last-step logits."""

    def __init__(self, decoder):
        super().__init__()
        self.decoder = decoder

    def forward(self, input_ids, encoder_hidden_states):
        logits = self.decoder(
            input_ids=input_ids,
            encoder_hidden_states=encoder_hidden_states,
            use_cache=False,
        ).logits
        return logits[:, -1:, :]


def test_image():
    """The same crude 'HI' used to smoke-test recognition."""
    strokes = [
        [(100, 100), (110, 180), (100, 300)],
        [(260, 100), (250, 200), (260, 300)],
        [(105, 200), (200, 205), (255, 200)],
        [(360, 120), (360, 290)],
        [(330, 120), (390, 120)],
        [(330, 290), (390, 290)],
    ]
    image = handwrite_ocr.build_ink_image(strokes)
    return handwrite_ocr.split_lines(image)[0]


def preprocess(image, size, mean, std):
    image = image.convert('RGB').resize((size[1], size[0]), Image.BICUBIC)
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
    return array.transpose(2, 0, 1)[None, ...]


def greedy_onnx(encoder, decoder, pixel_values, start, eos, max_new_tokens):
    hidden = encoder.run(None, {'pixel_values': pixel_values})[0]
    ids = [start]
    for _ in range(max_new_tokens):
        logits = decoder.run(
            None,
            {
                'input_ids': np.asarray([ids], dtype=np.int64),
                'encoder_hidden_states': hidden,
            },
        )[0]
        next_id = int(np.argmax(logits[0, -1]))
        if next_id == eos:
            break
        ids.append(next_id)
    return ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='microsoft/trocr-small-handwritten')
    parser.add_argument(
        '--output',
        default=os.path.expanduser(
            '~/.local/share/handwrite-input/models/trocr-small-onnx'
        ),
    )
    args = parser.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    print(f'Loading {args.model} ...')
    tokenizer = XLMRobertaTokenizer.from_pretrained(args.model)
    image_processor = ViTImageProcessor.from_pretrained(args.model)
    model = VisionEncoderDecoderModel.from_pretrained(args.model)
    model.eval()

    # The exact tokenizers backend the slow tokenizer wraps; saving it means the
    # runtime needs neither transformers nor the sentencepiece offset rules.
    tokenizer._tokenizer.save(str(out / 'tokenizer.json'))

    size = image_processor.size
    if hasattr(size, 'height'):
        size = (size.height, size.width)
    elif isinstance(size, dict):
        size = (size['height'], size['width'])
    else:
        size = (size, size)
    mean = image_processor.image_mean
    std = image_processor.image_std
    resample = image_processor.resample

    image = test_image()
    pixel_values = preprocess(image, size, mean, std)
    pixel_tensor = torch.from_numpy(pixel_values)

    with torch.no_grad():
        encoder_hidden = model.encoder(pixel_values=pixel_tensor).last_hidden_state
        logits = model.decoder(
            input_ids=torch.tensor([[2]], dtype=torch.long),
            encoder_hidden_states=encoder_hidden,
            use_cache=False,
        ).logits
        print('torch shapes:', tuple(encoder_hidden.shape), tuple(logits.shape))

    encoder_path = out / 'encoder_model.onnx'
    decoder_path = out / 'decoder_model.onnx'

    print('Exporting encoder ...')
    t = time.time()
    torch.onnx.export(
        Encoder(model.encoder),
        (pixel_tensor,),
        str(encoder_path),
        input_names=['pixel_values'],
        output_names=['last_hidden_state'],
        dynamic_axes={
            'pixel_values': {0: 'batch'},
            'last_hidden_state': {0: 'batch'},
        },
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f'  {encoder_path.name}: {encoder_path.stat().st_size / 1e6:.1f} MB in {time.time() - t:.1f}s')

    print('Exporting decoder ...')
    t = time.time()
    dummy_ids = torch.tensor([[2, 1241]], dtype=torch.long)
    torch.onnx.export(
        Decoder(model.decoder),
        (dummy_ids, encoder_hidden),
        str(decoder_path),
        input_names=['input_ids', 'encoder_hidden_states'],
        output_names=['logits'],
        dynamic_axes={
            'input_ids': {0: 'batch', 1: 'sequence'},
            'encoder_hidden_states': {0: 'batch'},
            'logits': {0: 'batch', 1: 'sequence'},
        },
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    print(f'  {decoder_path.name}: {decoder_path.stat().st_size / 1e6:.1f} MB in {time.time() - t:.1f}s')

    import onnxruntime as ort

    encoder = ort.InferenceSession(str(encoder_path), providers=['CPUExecutionProvider'])
    decoder = ort.InferenceSession(str(decoder_path), providers=['CPUExecutionProvider'])

    onnx_hidden = encoder.run(None, {'pixel_values': pixel_values})[0]
    print('encoder parity:', np.allclose(onnx_hidden, encoder_hidden.numpy(), atol=2e-3))

    started = time.time()
    onnx_ids = greedy_onnx(
        encoder,
        decoder,
        pixel_values,
        model.generation_config.decoder_start_token_id,
        tokenizer.eos_token_id,
        64,
    )
    onnx_time = time.time() - started

    # Exporting can leave the module's training flag changed; make sure
    # generate() still runs with dropout disabled before comparing.
    model.eval()
    started = time.time()
    with torch.inference_mode():
        torch_ids = model.generate(pixel_tensor, max_new_tokens=64)[0].tolist()
    torch_time = time.time() - started

    print('torch ids:', torch_ids)
    print('onnx  ids:', onnx_ids)
    # generate() appends the EOS token; the greedy loop stops before adding it.
    parity = onnx_ids == torch_ids or (
        torch_ids and torch_ids[-1] == tokenizer.eos_token_id and onnx_ids == torch_ids[:-1]
    )
    print('greedy parity:', parity)
    print(f'timing: onnx {onnx_time:.2f}s, torch {torch_time:.2f}s')
    print('text:', repr(tokenizer.decode(onnx_ids, skip_special_tokens=True)))

    config = {
        'model': args.model,
        'encoder': encoder_path.name,
        'decoder': decoder_path.name,
        'tokenizer': 'tokenizer.json',
        'decoder_start_token_id': 2,
        'eos_token_id': tokenizer.eos_token_id,
        'pad_token_id': tokenizer.pad_token_id,
        'max_new_tokens': 64,
        'image_size': size,
        'image_mean': list(mean),
        'image_std': list(std),
        'resample': resample,
    }
    (out / 'onnx_config.json').write_text(json.dumps(config, indent=2) + '\n')
    print('wrote', out / 'onnx_config.json')


if __name__ == '__main__':
    main()
