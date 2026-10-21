"""TrOCR inference with onnxruntime.

Runtime counterpart of tools/export_trocr_onnx.py: no torch, transformers or
Hugging Face Hub access.  The encoder runs once per line; the decoder is rerun
over the growing token prefix for each step (the checkpoint's generation
config sets use_cache=false, so this is what PyTorch's generate() did too).

The image preparation mirrors the checkpoint's DeiTImageProcessor:
RGB, bicubic resize to 384x384, rescale to [0,1], normalize with mean/std 0.5.
"""

import json
import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

import handwrite_ocr

DEFAULT_MODEL_ID = 'microsoft/trocr-small-handwritten'
DEFAULT_MODEL_DIR = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'handwrite-input' / 'models' / 'trocr-small-onnx'


def model_dir(model_id=None):
    """Directory of the ONNX export for `model_id`.

    HANDWRITE_ONNX_MODEL overrides the lookup, which is handy for testing an
    export from somewhere else.
    """
    override = os.environ.get('HANDWRITE_ONNX_MODEL')
    if override:
        return Path(override).expanduser()

    model_id = model_id or DEFAULT_MODEL_ID
    if model_id == DEFAULT_MODEL_ID:
        return DEFAULT_MODEL_DIR
    raise FileNotFoundError(
        f'no ONNX export for {model_id!r}; only {DEFAULT_MODEL_ID!r} is installed '
        '(see tools/export_trocr_onnx.py)'
    )


class OnnxTrOCR:
    def __init__(self, model_id=None, path=None):
        self.path = Path(path or model_dir(model_id))
        config_path = self.path / 'onnx_config.json'
        if not config_path.exists():
            raise FileNotFoundError(
                f'{config_path} not found; run tools/export_trocr_onnx.py first'
            )
        self.config = json.loads(config_path.read_text())

        options = ort.SessionOptions()
        # Leave a couple of cores for the rest of the session; recognition is
        # bursty and short, so latency matters more than throughput.
        cores = os.cpu_count() or 1
        options.intra_op_num_threads = max(1, min(6, cores - 1))
        providers = ['CPUExecutionProvider']
        self.encoder = ort.InferenceSession(
            str(self.path / self.config['encoder']),
            sess_options=options,
            providers=providers,
        )
        self.decoder = ort.InferenceSession(
            str(self.path / self.config['decoder']),
            sess_options=options,
            providers=providers,
        )
        self.tokenizer = Tokenizer.from_file(str(self.path / self.config['tokenizer']))
        self.start_id = int(self.config['decoder_start_token_id'])
        self.eos_id = int(self.config['eos_token_id'])
        self.max_new_tokens = int(self.config.get('max_new_tokens', 64))
        self.image_size = self.config.get('image_size', 384)
        if isinstance(self.image_size, int):
            self.image_size = (self.image_size, self.image_size)
        self.image_mean = np.asarray(self.config['image_mean'], dtype=np.float32)
        self.image_std = np.asarray(self.config['image_std'], dtype=np.float32)
        self.resample = int(self.config.get('resample', Image.BICUBIC))

    def preprocess(self, image):
        image = image.convert('RGB').resize(
            (self.image_size[1], self.image_size[0]), self.resample
        )
        array = np.asarray(image, dtype=np.float32) / 255.0
        array = (array - self.image_mean) / self.image_std
        return array.transpose(2, 0, 1)[None, ...]

    def read(self, image):
        """Read one text-line image and return its text."""
        pixel_values = self.preprocess(image)
        encoder_hidden = self.encoder.run(
            None, {'pixel_values': pixel_values}
        )[0]

        ids = [self.start_id]
        for _ in range(self.max_new_tokens):
            logits = self.decoder.run(
                None,
                {
                    'input_ids': np.asarray([ids], dtype=np.int64),
                    'encoder_hidden_states': encoder_hidden,
                },
            )[0]
            next_id = int(np.argmax(logits[0, -1]))
            if next_id == self.eos_id:
                break
            ids.append(next_id)

        text = self.tokenizer.decode(ids[1:], skip_special_tokens=True)
        return handwrite_ocr.tidy_line(text.strip())

    def read_lines(self, lines, batched=False):
        """Read several line images and join them with single spaces."""
        # Batching was measured slower for TrOCR (the processor pads every line
        # to the widest one); `batched` is accepted for API compatibility.
        parts = (self.read(line) for line in lines)
        return ' '.join(part for part in parts if part)
