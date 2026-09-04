"""JavaScript and TypeScript code analyzer for Knowledge Graph extraction."""

import re
from typing import List, Tuple
from backend.languages.interface import ExtractedRelation, ExtractedSymbol, LanguageAnalyzer


class JavaScriptAnalyzer(LanguageAnalyzer):
    """Parses JavaScript and TypeScript source files using regex/structural heuristics."""

    SUPPORTED_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

    def supports(self, file_path: str) -> bool:
        return any(file_path.endswith(ext) for ext in self.SUPPORTED_EXTENSIONS)

    def parse_file(
        self, file_path: str, source_code: str
    ) -> Tuple[List[ExtractedSymbol], List[ExtractedRelation]]:
        symbols: List[ExtractedSymbol] = []
        relations: List[ExtractedRelation] = []

        lines = source_code.splitlines()
        module_name = file_path.rsplit(".", 1)[0].replace("/", ".").replace("\\", ".")

        # Add module symbol
        symbols.append(
            ExtractedSymbol(
                name=file_path.split("/")[-1],
                qualified_name=module_name,
                symbol_type="module",
                file_path=file_path,
                start_line=1,
                end_line=max(1, len(lines)),
            )
        )

        current_class: str | None = None
        current_class_end: int = 0

        # Patterns
        func_decl_pattern = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(([^)]*)\)")
        arrow_func_pattern = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>")
        class_decl_pattern = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([a-zA-Z0-9_$]+)(?:\s+extends\s+([a-zA-Z0-9_$]+))?")
        method_decl_pattern = re.compile(r"^\s*(?:async\s+)?(?:get\s+|set\s+)?([a-zA-Z0-9_$]+)\s*\(([^)]*)\)\s*\{")
        import_pattern = re.compile(r"""(?:import\s+(?:(?:\{[^}]*\}|\*\s+as\s+[^,]+|[a-zA-Z0-9_$]+)\s*,?\s*)*(?:from\s+)?['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\s*\))""")
        test_pattern = re.compile(r"^\s*(?:it|test|describe)\s*\(\s*['\"`]([^'\"`]+)['\"`]")

        for line_num, line in enumerate(lines, start=1):
            # Check test declarations
            test_match = test_pattern.match(line)
            if test_match:
                test_title = test_match.group(1)
                test_qname = f"{module_name}.test.{test_title}"
                symbols.append(
                    ExtractedSymbol(
                        name=test_title,
                        qualified_name=test_qname,
                        symbol_type="test",
                        file_path=file_path,
                        start_line=line_num,
                        end_line=line_num,
                    )
                )
                relations.append(
                    ExtractedRelation(
                        source_qualified_name=module_name,
                        target_qualified_name=test_qname,
                        relation_type="defines",
                        file_path=file_path,
                        line_number=line_num,
                    )
                )
                continue

            # Check class declarations
            class_match = class_decl_pattern.match(line)
            if class_match:
                class_name = class_match.group(1)
                super_class = class_match.group(2)
                qname = f"{module_name}.{class_name}"
                current_class = class_name
                current_class_end = line_num + 50

                symbols.append(
                    ExtractedSymbol(
                        name=class_name,
                        qualified_name=qname,
                        symbol_type="class",
                        file_path=file_path,
                        start_line=line_num,
                        end_line=line_num,
                    )
                )
                relations.append(
                    ExtractedRelation(
                        source_qualified_name=module_name,
                        target_qualified_name=qname,
                        relation_type="defines",
                        file_path=file_path,
                        line_number=line_num,
                    )
                )
                if super_class:
                    relations.append(
                        ExtractedRelation(
                            source_qualified_name=qname,
                            target_qualified_name=super_class,
                            relation_type="inherits",
                            file_path=file_path,
                            line_number=line_num,
                        )
                    )
                continue

            # Check function declarations
            func_match = func_decl_pattern.match(line) or arrow_func_pattern.match(line)
            if func_match:
                fn_name = func_match.group(1)
                qname = f"{module_name}.{fn_name}"
                symbols.append(
                    ExtractedSymbol(
                        name=fn_name,
                        qualified_name=qname,
                        symbol_type="function",
                        file_path=file_path,
                        start_line=line_num,
                        end_line=line_num,
                    )
                )
                relations.append(
                    ExtractedRelation(
                        source_qualified_name=module_name,
                        target_qualified_name=qname,
                        relation_type="defines",
                        file_path=file_path,
                        line_number=line_num,
                    )
                )
                continue

            # Check class methods
            if current_class and line_num <= current_class_end:
                method_match = method_decl_pattern.match(line)
                if method_match and method_match.group(1) not in ("if", "for", "while", "switch", "catch"):
                    m_name = method_match.group(1)
                    qname = f"{module_name}.{current_class}.{m_name}"
                    symbols.append(
                        ExtractedSymbol(
                            name=m_name,
                            qualified_name=qname,
                            symbol_type="method",
                            file_path=file_path,
                            start_line=line_num,
                            end_line=line_num,
                        )
                    )
                    relations.append(
                        ExtractedRelation(
                            source_qualified_name=f"{module_name}.{current_class}",
                            target_qualified_name=qname,
                            relation_type="defines",
                            file_path=file_path,
                            line_number=line_num,
                        )
                    )

            # Check imports
            import_matches = import_pattern.findall(line)
            for imp in import_matches:
                target_mod = imp[0] or imp[1]
                if target_mod:
                    relations.append(
                        ExtractedRelation(
                            source_qualified_name=module_name,
                            target_qualified_name=target_mod,
                            relation_type="imports",
                            file_path=file_path,
                            line_number=line_num,
                        )
                    )

        return symbols, relations
