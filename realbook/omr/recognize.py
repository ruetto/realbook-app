"""Run the JAZZMUS recognizer (exported to ONNX) on one staff image -> Humdrum **kern text.

The model and weights are from the JAZZMUS project (University of Alicante / JKU Linz,
ISMIR 2025, MIT license). Exported graphs were checked to give token-for-token identical
output to the original PyTorch model.
"""
from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


def models_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "models"


class Recognizer:
    def __init__(self, folder: Path | None = None, threads: int = 0):
        import onnxruntime as ort

        folder = folder or models_dir()
        opts = ort.SessionOptions()
        if threads:
            opts.intra_op_num_threads = threads
        self.enc = ort.InferenceSession(str(folder / "encoder.onnx"), opts, providers=["CPUExecutionProvider"])
        self.dec = ort.InferenceSession(str(folder / "decoder_step.onnx"), opts, providers=["CPUExecutionProvider"])
        voc = json.loads((folder / "vocab.json").read_text())
        self.w2i, self.i2w = voc["w2i"], voc["i2w"]
        self.maxlen = int(voc["maxlen"])
        self.h, self.max_w = int(voc["img_height"]), int(voc["max_width"])

    def preprocess(self, img: Image.Image) -> np.ndarray:
        import cv2

        a = np.asarray(img.convert("L"))
        width = int(np.ceil(a.shape[1] * self.h / a.shape[0]))
        width = min(width, self.max_w)
        a = cv2.resize(a, (width, self.h))  # same call and interpolation as the original pipeline
        return (a.astype(np.float32) / 255.0)[None, None]

    def read(self, img: Image.Image) -> tuple[list[str], float, float]:
        """Greedy decoding. Returns tokens, mean confidence of the chosen tokens, and the share of tokens
        chosen with less than 50 % confidence (high on things that aren't music, such as titles)."""
        enc = self.enc.run(None, {"image": self.preprocess(img)})[0]
        seq = [self.w2i["<bos>"]]
        out, probs = [], []
        for _ in range(self.maxlen - 1):
            logits = self.dec.run(None, {"enc": enc, "tokens": np.array([seq], dtype=np.int64)})[0][0]
            t = int(np.argmax(logits))
            e = np.exp(logits - logits[t])
            probs.append(float(1.0 / e.sum()))
            word = self.i2w[str(t)]
            if word == "<eos>":
                break
            out.append(word)
            seq.append(t)
        p = np.array(probs) if probs else np.array([0.0])
        return out, float(p.mean()), float((p < 0.5).mean())

    def tokens(self, img: Image.Image) -> list[str]:
        return self.read(img)[0]

    def kern(self, img: Image.Image) -> str:
        return untokenize(self.tokens(img))

    def kern_checked(self, img: Image.Image) -> tuple[str, bool]:
        """Kern text and whether the reading looks like real music (confident)."""
        toks, mean, low = self.read(img)
        return untokenize(toks), (mean >= 0.9 and low <= 0.08)


def untokenize(tokens: list[str]) -> str:
    return ("".join(tokens).replace("<t>", "\t").replace("<n>", "\n").replace("<s>", " ")
            .replace("<chord-pitch>", "").replace("<chord-extension>", ""))


@lru_cache(maxsize=1)
def default_recognizer() -> Recognizer:
    return Recognizer()
