"""ChartConditionNet — ResNet-50 transfer model for chart-condition scoring.

Two heads over a shared backbone:
- score head: single raw logit -> tanh -> condition score in [-1, 1]
- pattern head: 5 logits -> sigmoid -> multi-label pattern probabilities
  (support_bounce, resistance_rejection, breakout_up, breakdown, consolidation)

Torch/torchvision are imported lazily so the service can run on the
heuristic backend without the ML stack installed.
"""
from __future__ import annotations

import io

N_PATTERNS = 5
IMAGE_SIZE = 224
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


def build_model(pretrained: bool = True, n_patterns: int = N_PATTERNS):
    import torch.nn as nn
    from torchvision.models import ResNet50_Weights, resnet50

    class ChartConditionNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            backbone = resnet50(
                weights=ResNet50_Weights.IMAGENET1K_V2 if pretrained else None
            )
            feat_dim = backbone.fc.in_features
            backbone.fc = nn.Identity()
            self.backbone = backbone
            self.score_head = nn.Sequential(
                nn.Dropout(0.2), nn.Linear(feat_dim, 1)
            )
            self.pattern_head = nn.Sequential(
                nn.Dropout(0.2), nn.Linear(feat_dim, n_patterns)
            )

        def forward(self, x):
            feats = self.backbone(x)
            return self.score_head(feats).squeeze(-1), self.pattern_head(feats)

    return ChartConditionNet()


def preprocess(image_bytes: bytes):
    """Bytes -> normalized CHW float tensor ready for ChartConditionNet."""
    from PIL import Image
    from torchvision import transforms

    tfm = transforms.Compose(
        [
            transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize(_IMAGENET_MEAN, _IMAGENET_STD),
        ]
    )
    return tfm(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
