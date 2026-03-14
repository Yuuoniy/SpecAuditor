from functools import lru_cache

from tree_sitter import Language, Parser

try:
    from .artifact_utils import get_build_library_path, get_tree_sitter_dir
except ImportError:
    from artifact_utils import get_build_library_path, get_tree_sitter_dir


@lru_cache(maxsize=1)
def get_parser(language_name):
    build_path = get_build_library_path(__file__)
    grammar_path = get_tree_sitter_dir(__file__)

    if not build_path.exists():
        build_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            Language.build_library(str(build_path), [str(grammar_path)])
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError(
                "Building the tree-sitter parser requires setuptools on Python 3.13+. "
                "Install the project dependencies from requirements.txt before running the artifact."
            ) from exc

    C_LANGUAGE = Language(str(build_path), "c")
    parser = Parser()
    parser.set_language(C_LANGUAGE)
    return parser




LOG_FUNC = ['mtk_v4l2_err','dev_err','dev_err_probe','pr_debug']


class ASTParser:
    def __init__(self):
        pass
    
    @staticmethod
    def tree_sitter_init():
        # Use pre-built language instead of building from source
        # This avoids the distutils dependency issue in Python 3.12+
        return get_parser("c")
    
    @staticmethod
    def get_func_name_from_def(code):
        parser = ASTParser.tree_sitter_init()
        tree = parser.parse(bytes(code, "utf8"))
        funcs = ASTParser.find_node_by_type(tree,"function_declarator")
        if len(funcs) == 0:
            return ''

        return ASTParser.get_node_content(funcs[0].child_by_field_name("declarator"), code)

    @staticmethod
    def find_node_by_type(node, node_type):
        cursor = node.walk()
        if type(node_type) == str:
            node_type = [node_type]
        node_lst = []
        while True:
            if cursor.node.type in node_type:
                node_lst.append(cursor.node)
            if not cursor.goto_first_child():
                while not cursor.goto_next_sibling():
                    if not cursor.goto_parent():
                        return node_lst
                
    @staticmethod        
    def get_node_content(node, code):
        return code[node.start_byte : node.end_byte]
