import pytest
from backend.languages.python_analyzer import PythonAnalyzer

SAMPLE_CODE = """
import os
from payment.service import PaymentProcessor

class InvoiceService:
    def __init__(self, processor: PaymentProcessor):
        self.processor = processor

    def process_invoice(self, invoice_id: str) -> bool:
        return self.processor.charge(100)

def test_invoice_service():
    proc = PaymentProcessor()
    svc = InvoiceService(proc)
    assert svc.process_invoice("INV-123") is True
"""


def test_python_analyzer_extracts_symbols():
    analyzer = PythonAnalyzer()
    symbols, relations = analyzer.parse_file("services/invoice.py", SAMPLE_CODE)

    assert any(s.name == "InvoiceService" and s.symbol_type == "Class" for s in symbols)
    assert any(s.name == "process_invoice" and s.symbol_type == "Method" for s in symbols)
    assert any(s.name == "test_invoice_service" and s.symbol_type == "Test" for s in symbols)


def test_python_analyzer_extracts_relations():
    analyzer = PythonAnalyzer()
    symbols, relations = analyzer.parse_file("services/invoice.py", SAMPLE_CODE)

    imports = [r for r in relations if r.relation_type == "imports"]
    assert len(imports) >= 2
    assert any(r.target_qualified_name == "payment.service.PaymentProcessor" for r in imports)

    tests_rel = [r for r in relations if r.relation_type == "tests"]
    assert len(tests_rel) > 0
