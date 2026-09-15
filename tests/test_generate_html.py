from GenerateHTML import strip_imports_exports


def test_strip_imports_exports_flattens_es_module_syntax():
    src = (
        "import { formatYear } from './utils.js';\n"
        "  import Foo from './Foo.js';\n"
        "export default class Visualization { }\n"
        "export function helper() { }\n"
        "export const X = 1;\n"
        "export { helper, X };\n"
        "const keep = 'import me not';\n"
    )
    out = strip_imports_exports(src)
    assert "from './utils.js'" not in out
    assert "from './Foo.js'" not in out
    assert "export" not in out
    assert "class Visualization { }" in out
    assert "function helper() { }" in out
    assert "const X = 1;" in out
    assert "const keep = 'import me not';" in out
