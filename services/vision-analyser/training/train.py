"""Transfer-learning trainer for ChartConditionNet.

Dataset layout (CSV manifest):
    chart_path,score,support_bounce,resistance_rejection,breakout_up,breakdown,consolidation
    charts/nvda_2026-03-01.png,0.8,0,0,1,0,0

Score is the human-labeled condition in [-1, 1]; pattern columns are 0/1.

Usage:
    python training/train.py --manifest data/labels.csv --epochs 8 --out weights/chartnet.pt
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model import N_PATTERNS, build_model, preprocess  # noqa: E402


class ChartDataset(Dataset):
    def __init__(self, manifest: Path) -> None:
        with manifest.open(newline="", encoding="utf-8") as fh:
            self.rows = list(csv.DictReader(fh))
        self.base = manifest.parent

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int):
        row = self.rows[idx]
        image = preprocess((self.base / row["chart_path"]).read_bytes())
        score = torch.tensor(float(row["score"]), dtype=torch.float32)
        patterns = torch.tensor(
            [float(row[k]) for k in list(row)[2 : 2 + N_PATTERNS]],
            dtype=torch.float32,
        )
        return image, score, patterns


def train(manifest: Path, epochs: int, batch_size: int, lr: float, out: Path) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_model(pretrained=True).to(device)

    # freeze backbone for the first half of training (feature extraction),
    # unfreeze for the second half (fine-tuning)
    for p in model.backbone.parameters():
        p.requires_grad = False

    loader = DataLoader(
        ChartDataset(manifest), batch_size=batch_size, shuffle=True, num_workers=2
    )
    optimizer = torch.optim.AdamW(
        (p for p in model.parameters() if p.requires_grad), lr=lr
    )
    score_loss = nn.MSELoss()
    pattern_loss = nn.BCEWithLogitsLoss()

    for epoch in range(epochs):
        if epoch == epochs // 2:
            for p in model.backbone.parameters():
                p.requires_grad = True
            optimizer = torch.optim.AdamW(model.parameters(), lr=lr / 10)

        model.train()
        total = 0.0
        for images, scores, patterns in loader:
            images, scores, patterns = (
                images.to(device), scores.to(device), patterns.to(device),
            )
            optimizer.zero_grad()
            raw_score, pattern_logits = model(images)
            loss = score_loss(torch.tanh(raw_score), scores) + pattern_loss(
                pattern_logits, patterns
            )
            loss.backward()
            optimizer.step()
            total += loss.item() * images.size(0)
        print(f"epoch {epoch + 1}/{epochs}  loss={total / len(loader.dataset):.4f}")

    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out)
    print(f"saved weights -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--out", type=Path, default=Path("weights/chartnet.pt"))
    args = ap.parse_args()
    train(args.manifest, args.epochs, args.batch_size, args.lr, args.out)
