"""Bundled library gate: every field's presence is declared (docs/contract_conformance.md, D2/D6).

A field carrying an error-severity format rule and no presence decision means
one thing to an engine with implicit-required and another to Core. The bundled
library carries no such field: each has a not_empty / not_empty_string /
required_if rule, or its format rule says `optional: true` (or is conditional).
The linter's FORMAT_ONLY_FIELD_ACCEPTS_EMPTY (info) names offenders; here it
is an error for the library.
"""
from pathlib import Path

import pytest

from opendqv.core.linter import lint_contract_yaml

CONTRACTS = sorted((Path(__file__).resolve().parents[1] / "opendqv" / "contracts").glob("*.yaml"))


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.stem)
def test_every_field_presence_is_explicit(path: Path):
    result = lint_contract_yaml(path.read_text(encoding="utf-8"), contract_name=path.stem)
    offenders = [i for i in result.issues if i.code == "FORMAT_ONLY_FIELD_ACCEPTS_EMPTY"]
    assert not offenders, (
        f"{path.name}: fields with format rules but no presence decision: "
        + "; ".join(i.message for i in offenders)
    )
