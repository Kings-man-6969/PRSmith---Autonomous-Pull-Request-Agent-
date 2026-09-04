"""Python AST Analyzer for extracting symbols, call graphs, imports, and test mappings."""

import ast
from typing import Any, Dict, List, Optional, Set, Tuple
from backend.languages.interface import ExtractedRelation, ExtractedSymbol, LanguageAnalyzer
from backend.observability.logging import get_logger

logger = get_logger(__name__)


class PythonASTVisitor(ast.NodeVisitor):
    """Walks the Python AST to discover symbols and relationships."""

    def __init__(self, file_path: str, module_name: str):
        self.file_path = file_path
        self.module_name = module_name
        self.symbols: List[ExtractedSymbol] = []
        self.relations: List[ExtractedRelation] = []
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        self.imports: Dict[str, str] = {}  # alias -> full module/symbol

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.name
            asname = alias.asname or name
            self.imports[asname] = name
            self.relations.append(
                ExtractedRelation(
                    source_qualified_name=self.module_name,
                    target_qualified_name=name,
                    relation_type="imports",
                    file_path=self.file_path,
                    line_number=node.lineno,
                    derived_by="python_ast",
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            asname = alias.asname or alias.name
            self.imports[asname] = target
            self.relations.append(
                ExtractedRelation(
                    source_qualified_name=self.module_name,
                    target_qualified_name=target,
                    relation_type="imports",
                    file_path=self.file_path,
                    line_number=node.lineno,
                    derived_by="python_ast",
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prev_class = self.current_class
        class_name = node.name
        qualified_name = (
            f"{self.module_name}.{prev_class}.{class_name}"
            if prev_class
            else f"{self.module_name}.{class_name}"
        )
        self.current_class = class_name

        # Extract base classes
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_target = self.imports.get(base.id, base.id)
                self.relations.append(
                    ExtractedRelation(
                        source_qualified_name=qualified_name,
                        target_qualified_name=base_target,
                        relation_type="inherits",
                        file_path=self.file_path,
                        line_number=node.lineno,
                        derived_by="python_ast",
                    )
                )

        decorators = [ast.unparse(d) for d in node.decorator_list if hasattr(ast, "unparse")]

        self.symbols.append(
            ExtractedSymbol(
                name=class_name,
                qualified_name=qualified_name,
                symbol_type="Class",
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                docstring=ast.get_docstring(node),
                decorators=decorators,
            )
        )

        self.generic_visit(node)
        self.current_class = prev_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_function(node, is_async=True)

    def _handle_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool = False
    ) -> None:
        prev_func = self.current_function
        func_name = node.name

        if self.current_class:
            qualified_name = f"{self.module_name}.{self.current_class}.{func_name}"
            symbol_type = "Method"
        else:
            qualified_name = f"{self.module_name}.{func_name}"
            symbol_type = "Function"

        # Check if it is a test
        is_test = func_name.startswith("test_") or "test" in self.file_path.lower()
        if is_test:
            symbol_type = "Test"

        # Extract parameters
        params = [arg.arg for arg in node.args.args]

        # Extract return type
        return_type = None
        if node.returns and hasattr(ast, "unparse"):
            return_type = ast.unparse(node.returns)

        # Extract decorators
        decorators = [ast.unparse(d) for d in node.decorator_list if hasattr(ast, "unparse")]

        # Check for API endpoint decorators (@app.get, @router.post, etc.)
        for dec in decorators:
            if any(method in dec.lower() for method in ["get(", "post(", "put(", "delete(", "patch(", "route("]):
                if symbol_type != "Test":
                    symbol_type = "APIEndpoint"

        self.symbols.append(
            ExtractedSymbol(
                name=func_name,
                qualified_name=qualified_name,
                symbol_type=symbol_type,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                docstring=ast.get_docstring(node),
                parameters=params,
                return_type=return_type,
                decorators=decorators,
                properties={"is_async": is_async, "is_test": is_test},
            )
        )

        self.current_function = qualified_name
        self.generic_visit(node)
        self.current_function = prev_func

    def visit_Call(self, node: ast.Call) -> None:
        caller = self.current_function or self.current_class or self.module_name
        callee_name = None

        if isinstance(node.func, ast.Name):
            callee_name = self.imports.get(node.func.id, node.func.id)
        elif isinstance(node.func, ast.Attribute):
            if hasattr(ast, "unparse"):
                callee_name = ast.unparse(node.func)
            else:
                callee_name = node.func.attr

        if callee_name:
            rel_type = "calls"
            is_direct = isinstance(node.func, ast.Name)
            provenance = "DIRECT_STATIC" if is_direct else "INFERRED"
            weight = 1.0 if is_direct else 0.7

            # Detect database operations
            if any(op in callee_name.lower() for op in [".query", ".filter", ".select", ".find", ".get"]):
                rel_type = "reads_db"
            elif any(op in callee_name.lower() for op in [".commit", ".add", ".save", ".delete", ".update"]):
                rel_type = "writes_db"

            # If caller is a test, mark relationship as 'tests'
            if "test" in caller.lower():
                rel_type = "tests"

            self.relations.append(
                ExtractedRelation(
                    source_qualified_name=caller,
                    target_qualified_name=callee_name,
                    relation_type=rel_type,
                    file_path=self.file_path,
                    line_number=node.lineno,
                    derived_by="python_ast",
                    edge_provenance=provenance,
                    provenance_weight=weight,
                )
            )

        self.generic_visit(node)


class PythonAnalyzer(LanguageAnalyzer):
    """Deterministic AST parser for Python source files."""

    def supports(self, file_path: str) -> bool:
        return file_path.endswith(".py")

    def parse_file(
        self, file_path: str, source_code: str
    ) -> Tuple[List[ExtractedSymbol], List[ExtractedRelation]]:
        if not self.supports(file_path):
            return [], []

        module_name = file_path.replace("/", ".").replace("\\", ".").rstrip(".py")
        try:
            tree = ast.parse(source_code, filename=file_path)
            visitor = PythonASTVisitor(file_path, module_name)
            visitor.visit(tree)
            return visitor.symbols, visitor.relations
        except SyntaxError as e:
            logger.warning(
                "Syntax error while parsing Python AST",
                file_path=file_path,
                line=e.lineno,
                error=str(e),
            )
            return [], []
        except Exception as e:
            logger.error("Failed to parse Python AST", file_path=file_path, error=str(e))
            return [], []
