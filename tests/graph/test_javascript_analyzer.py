"""Tests for JavaScript/TypeScript AST analyzer."""

from backend.languages.javascript_analyzer import JavaScriptAnalyzer


def test_js_analyzer_extracts_symbols_and_relations():
    analyzer = JavaScriptAnalyzer()
    assert analyzer.supports("src/index.js")
    assert analyzer.supports("components/App.tsx")
    assert not analyzer.supports("main.py")

    sample_code = """
import express from 'express';
import { helper } from './utils';

class Server extends BaseServer {
  start(port) {
    console.log('running');
  }
}

function processTask(taskId) {
  return taskId + 1;
}

const handleRequest = async (req, res) => {
  return res.send('ok');
};

describe('Server Test', () => {
  it('should start properly', () => {
    expect(true).toBe(true);
  });
});
"""

    symbols, relations = analyzer.parse_file("src/server.js", sample_code)
    symbol_names = {s.name for s in symbols}

    assert "Server" in symbol_names
    assert "start" in symbol_names
    assert "processTask" in symbol_names
    assert "handleRequest" in symbol_names
    assert "Server Test" in symbol_names or "should start properly" in symbol_names

    relation_types = {r.relation_type for r in relations}
    assert "imports" in relation_types
    assert "defines" in relation_types
    assert "inherits" in relation_types
