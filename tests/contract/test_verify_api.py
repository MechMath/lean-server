from __future__ import annotations

import json
from pathlib import Path
import unittest

from lean_server.http import RequestValidationError, VerifyProofRequest
from tests.lean.schema_assertions import assert_matches_schema


SCHEMA = json.loads((Path(__file__).parents[2] /
                    "protocol/http/verify-proof-request.schema.json").read_text())
BASE = {
    "formal_statement": "theorem target : True := True.intro",
    "content": "theorem target : True := by constructor",
    "environment": "lean-4.30.0",
}


class VerifyAPIContractTests(unittest.TestCase):
    def test_supported_modes_match_schema_and_http_parser(self) -> None:
        explicit_defaults = {name: rule["default"] for name, rule in SCHEMA["properties"].items()
                             if "default" in rule}
        for options in ({}, explicit_defaults, {**explicit_defaults, "use_def_eq": False},
                        {"timeout_seconds": 0.01}, {"timeout_seconds": 600}):
            with self.subTest(options=options):
                request = {**BASE, **options}
                assert_matches_schema(request, SCHEMA)
                parsed = VerifyProofRequest.from_dict(request)
                self.assertEqual(parsed.use_def_eq, options.get("use_def_eq", True))
                self.assertEqual(parsed.timeout_seconds, options.get("timeout_seconds", 600))

    def test_unsupported_modes_have_stable_errors_and_fail_schema(self) -> None:
        for options, message in (
            ({"permitted_sorries": ["target"]}, "non-empty permitted_sorries is not supported"),
            ({"mathlib_options": True}, "mathlib_options=true is not supported"),
            ({"global_options": {"maxHeartbeats": 0}}, "non-empty global_options is not supported"),
            ({"verify_negation": True}, "verify_negation=true is not supported"),
            ({"ignore_imports": False}, "ignore_imports=false is not supported"),
            ({"unexpected": True}, "unsupported request field(s): unexpected"),
        ):
            with self.subTest(options=options):
                request = {**BASE, **options}
                with self.assertRaises(AssertionError):
                    assert_matches_schema(request, SCHEMA)
                with self.assertRaises(RequestValidationError) as caught:
                    VerifyProofRequest.from_dict(request)
                self.assertEqual(str(caught.exception), message)

    def test_option_type_errors_are_rejected(self) -> None:
        for field, value in (("permitted_sorries", [1]), ("global_options", []),
                             ("mathlib_options", 0), ("verify_negation", 0),
                             ("ignore_imports", 1), ("use_def_eq", 1),
                             ("timeout_seconds", True), ("timeout_seconds", 0)):
            with self.subTest(field=field, value=value):
                request = {**BASE, field: value}
                with self.assertRaises(AssertionError):
                    assert_matches_schema(request, SCHEMA)
                with self.assertRaises(RequestValidationError):
                    VerifyProofRequest.from_dict(request)

    def test_schema_leaves_timeout_maximum_to_deployment(self) -> None:
        request = {**BASE, "timeout_seconds": 1800}
        assert_matches_schema(request, SCHEMA)
        with self.assertRaises(RequestValidationError):
            VerifyProofRequest.from_dict(request)
        self.assertEqual(VerifyProofRequest.from_dict(request, max_timeout_seconds=1800).timeout_seconds,
                         1800)
