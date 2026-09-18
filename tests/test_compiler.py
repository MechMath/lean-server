import unittest

from lean_server.compiler import _diagnostics


class DiagnosticTests(unittest.TestCase):
    def test_splits_warning_and_error_messages(self) -> None:
        output = "\n".join(
            [
                '{"severity":"warning","data":"unused variable","fileName":"<stdin>","pos":{"line":1,"column":4}}',
                '{"severity":"error","data":"type mismatch","fileName":"<stdin>","pos":{"line":2,"column":1}}',
            ]
        )
        warnings, errors = _diagnostics(output)
        self.assertEqual([item.message for item in warnings], ["unused variable"])
        self.assertEqual([item.message for item in errors], ["type mismatch"])

    def test_non_json_output_is_an_error(self) -> None:
        warnings, errors = _diagnostics("unexpected compiler output")
        self.assertEqual(warnings, [])
        self.assertEqual(errors[0].message, "unexpected compiler output")


if __name__ == "__main__":
    unittest.main()
