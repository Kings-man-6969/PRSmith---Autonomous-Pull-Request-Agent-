import pytest
from backend.validation.classifiers import FailureClassifier, FailureClass


def test_classify_oom():
    res = FailureClassifier.classify("test", 137, "", "Process killed: out of memory")
    assert res == FailureClass.OOM
    assert FailureClassifier.is_repairable(res) is False


def test_classify_test_failure():
    res = FailureClassifier.classify("targeted_test", 1, "FAILED test_payment.py::test_charge", "")
    assert res == FailureClass.TEST_FAILURE
    assert FailureClassifier.is_repairable(res) is True


def test_classify_type_error():
    res = FailureClassifier.classify("type_check", 1, "service.py:12: error: Incompatible type", "")
    assert res == FailureClass.TYPE_ERROR
    assert FailureClassifier.is_repairable(res) is True
