from __future__ import annotations

from pathlib import Path

from scripts.check_workflow_pins import check_workflow_paths

CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def _violations(tmp_path: Path, workflow: str) -> list[str]:
    path = tmp_path / "workflow.yml"
    path.write_text(workflow, encoding="utf-8")
    return check_workflow_paths([path])


def test_rejects_movable_tag(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        """jobs:
  test:
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
""",
    )

    assert any("immutable" in violation for violation in violations)


def test_rejects_short_sha(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        """jobs:
  test:
    steps:
      - uses: actions/setup-python@abc123
""",
    )

    assert any("40 lowercase hexadecimal" in violation for violation in violations)


def test_rejects_uppercase_or_non_hex_sha(tmp_path: Path) -> None:
    uppercase = "A" * 40
    non_hex = "g" * 40

    for reference in (uppercase, non_hex):
        violations = _violations(
            tmp_path,
            f"""jobs:
  test:
    steps:
      - uses: actions/setup-python@{reference}
""",
        )
        assert any("40 lowercase hexadecimal" in violation for violation in violations)


def test_rejects_checkout_without_non_persistent_credentials(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
""",
    )

    assert any("persist-credentials: false" in violation for violation in violations)


def test_accepts_compliant_external_and_local_actions(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@{SETUP_PYTHON_SHA} # v7.0.0
      - uses: ./actions/local
""",
    )

    assert violations == []


def test_rejects_flow_style_uses_mapping(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        """jobs:
  test:
    steps:
      - {uses: actions/checkout@v7, with: {persist-credentials: false}}
""",
    )

    assert any("unsupported" in violation for violation in violations)


def test_rejects_quoted_uses_key(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        """jobs:
  test:
    steps:
      - 'uses': actions/setup-python@v7
""",
    )

    assert any("unsupported" in violation for violation in violations)


def test_does_not_treat_block_scalar_text_as_checkout_input(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
        with:
          sparse-checkout: |
            persist-credentials: false
""",
    )

    assert any("persist-credentials: false" in violation for violation in violations)


def test_accepts_flow_style_with_mapping(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
        with: {{persist-credentials: false}}
""",
    )

    assert violations == []


def test_rejects_flow_style_uses_mapping_when_uses_is_not_first(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        """jobs:
  test:
    steps:
      - {name: bypass, uses: actions/checkout@v7}
""",
    )

    assert any("unsupported" in violation for violation in violations)


def test_does_not_treat_following_name_scalar_as_checkout_with(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
        name: |
          with:
            persist-credentials: false
""",
    )

    assert any("persist-credentials: false" in violation for violation in violations)


def test_ignores_over_indented_comments_and_nested_scalar_text(tmp_path: Path) -> None:
    violations = _violations(
        tmp_path,
        f"""jobs:
  test:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
        with:
            # over-indented comment
          sparse-checkout: |
            persist-credentials: false
""",
    )

    assert any("persist-credentials: false" in violation for violation in violations)
