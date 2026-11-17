from __future__ import annotations

from pathlib import Path

import pytest

from verityflow.config import Config
from verityflow.ir.model import Eq
from verityflow.models.dictionary import (
    DataDictionary,
    DestinationDecl,
    FieldDecl,
    FieldType,
)

ROOT = Path(__file__).resolve().parent.parent.parent

STARTER_TEXT = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def starter_dictionary() -> DataDictionary:
    return DataDictionary(
        fields=(
            FieldDecl(
                name="category",
                type=FieldType.STRING,
                domain=("billing", "technical", "general"),
            ),
            FieldDecl(
                name="priority",
                type=FieldType.STRING,
                domain=("urgent", "normal"),
            ),
            FieldDecl(name="wait_time", type=FieldType.INTEGER, unit="minutes"),
            FieldDecl(name="vip", type=FieldType.BOOLEAN),
            FieldDecl(name="amount", type=FieldType.DECIMAL, unit="usd", scale=2),
        ),
        destinations=(
            DestinationDecl(id="manual_review", aliases=("manual review",)),
            DestinationDecl(id="priority_queue", aliases=("priority queue",)),
            DestinationDecl(id="escalation_queue", aliases=("escalation queue",)),
            DestinationDecl(id="normal_queue", aliases=("normal queue",)),
            DestinationDecl(id="billing_queue", aliases=("billing queue",)),
        ),
        aliases={"waiting": "wait_time"},
        named_predicates={
            "urgent ticket": Eq(field="priority", value="urgent"),
            "billing ticket": Eq(field="category", value="billing"),
        },
    )


@pytest.fixture
def dictionary() -> DataDictionary:
    return starter_dictionary()


@pytest.fixture
def starter_text() -> str:
    return STARTER_TEXT


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def dev_bundles() -> Path:
    return ROOT / "fixtures" / "dev"


@pytest.fixture
def dev_evidence_bundles() -> Path:
    return ROOT / "fixtures" / "dev_evidence" / "holdout_v1" / "bundles"
