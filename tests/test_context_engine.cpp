// Mini framework de test para context::ContextEngine (FASE 8)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <regex>

#include "context/engine/ContextEngine.h"
#include "context/engine/ProjectContext.h"

using namespace satellite::context;

int g_passed = 0;
int g_failed = 0;

#define CHECK(desc, cond) \
    do { \
        if (cond) { \
            std::cout << "PASSED: " << desc << "\n"; \
            ++g_passed; \
        } else { \
            std::cout << "FAILED: " << desc << ": " #cond "\n"; \
            ++g_failed; \
        } \
    } while (false)

std::string create_test_project()
{
    auto tmp_base = std::filesystem::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_test_ctx_" + std::to_string(timestamp));

    std::filesystem::create_directories(tmp_dir / "src");
    std::filesystem::create_directories(tmp_dir / ".git");
    std::filesystem::create_directories(tmp_dir / "build");
    std::filesystem::create_directories(tmp_dir / "node_modules");

    {
        std::ofstream f(tmp_dir / "src" / "math.cpp");
        f << "#include \"math.h\"\n";
        f << "#include <vector>\n\n";
        f << "int add(int a, int b) { return a + b; }\n\n";
        f << "class Calculator {\n";
        f << "public:\n";
        f << "    int mul(int x, int y) { return x * y; }\n";
        f << "};\n";
    }

    {
        std::ofstream f(tmp_dir / "src" / "math.h");
        f << "#pragma once\n\n";
        f << "struct Point {\n";
        f << "    double x;\n";
        f << "    double y;\n";
        f << "};\n";
    }

    {
        std::ofstream f(tmp_dir / "app.py");
        f << "import os\n";
        f << "from datetime import datetime\n\n";
        f << "def main():\n";
        f << "    return 0\n\n";
        f << "class App:\n";
        f << "    def run(self):\n";
        f << "        pass\n";
    }

    {
        std::ofstream f(tmp_dir / "README.md");
        f << "# Test Project\n";
    }

    {
        std::ofstream f(tmp_dir / ".git" / "HEAD");
        f << "ref: refs/heads/main\n";
    }

    {
        std::ofstream f(tmp_dir / "build" / "obj.cpp");
        f << "// build artifact\n";
    }

    {
        std::ofstream f(tmp_dir / "node_modules" / "x.js");
        f << "// node module\n";
    }

    return tmp_dir.string();
}

void test_build_basic()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        ProjectContext ctx = ce.build();

        auto canonical_root = std::filesystem::canonical(tmp_dir).string();

        CHECK("ctx.total_files == 3", ctx.total_files == 3);
        CHECK("ctx.root == canonical(tmp_dir)", ctx.root == canonical_root);
        CHECK("ctx.total_lines > 0", ctx.total_lines > 0);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_build_basic: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_basic: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_files_info()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        ProjectContext ctx = ce.build();

        bool has_math_cpp = false;
        bool has_math_h = false;
        bool has_app_py = false;
        bool has_readme = false;
        bool has_git = false;
        bool has_build = false;
        bool has_node_modules = false;

        for (const auto& file : ctx.files)
        {
            if (file.path == "src/math.cpp")
            {
                has_math_cpp = true;
                CHECK("math.cpp language == C++", file.type == "C++");
            }
            else if (file.path == "src/math.h")
            {
                has_math_h = true;
                CHECK("math.h language == C++", file.type == "C++");
            }
            else if (file.path == "app.py")
            {
                has_app_py = true;
                CHECK("app.py language == Python", file.type == "Python");
            }
            else if (file.path.find("README.md") != std::string::npos)
            {
                has_readme = true;
            }
            else if (file.path.find(".git") != std::string::npos)
            {
                has_git = true;
            }
            else if (file.path.find("build") != std::string::npos)
            {
                has_build = true;
            }
            else if (file.path.find("node_modules") != std::string::npos)
            {
                has_node_modules = true;
            }
        }

        CHECK("existe FileInfo src/math.cpp", has_math_cpp);
        CHECK("existe FileInfo src/math.h", has_math_h);
        CHECK("existe FileInfo app.py", has_app_py);
        CHECK("NO existe FileInfo README.md", !has_readme);
        CHECK("NO existe FileInfo .git", !has_git);
        CHECK("NO existe FileInfo build", !has_build);
        CHECK("NO existe FileInfo node_modules", !has_node_modules);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_files_info: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_files_info: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_symbols()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        ProjectContext ctx = ce.build();

        bool has_add = false;
        bool has_calculator = false;
        bool has_point = false;
        bool has_main = false;
        bool has_app_class = false;

        for (const auto& file : ctx.files)
        {
            for (const auto& sym : file.symbols)
            {
                if (sym.name == "add" && sym.kind == SymbolKind::Function && sym.file == "src/math.cpp")
                {
                    has_add = true;
                    CHECK("symbol add line >= 1", sym.line >= 1);
                    CHECK("symbol add signature no vacia", !sym.signature.empty());
                }
                else if (sym.name == "Calculator" && sym.kind == SymbolKind::Class && sym.file == "src/math.cpp")
                {
                    has_calculator = true;
                    CHECK("symbol Calculator line >= 1", sym.line >= 1);
                    CHECK("symbol Calculator signature no vacia", !sym.signature.empty());
                }
                else if (sym.name == "Point" && sym.kind == SymbolKind::Class && sym.file == "src/math.h")
                {
                    has_point = true;
                    CHECK("symbol Point line >= 1", sym.line >= 1);
                    CHECK("symbol Point signature no vacia", !sym.signature.empty());
                }
                else if (sym.name == "main" && sym.kind == SymbolKind::Function && sym.file == "app.py")
                {
                    has_main = true;
                    CHECK("symbol main line >= 1", sym.line >= 1);
                    CHECK("symbol main signature no vacia", !sym.signature.empty());
                }
                else if (sym.name == "App" && sym.kind == SymbolKind::Class && sym.file == "app.py")
                {
                    has_app_class = true;
                    CHECK("symbol App line >= 1", sym.line >= 1);
                    CHECK("symbol App signature no vacia", !sym.signature.empty());
                }
            }
        }

        CHECK("existe SymbolInfo add Function src/math.cpp", has_add);
        CHECK("existe SymbolInfo Calculator Class src/math.cpp", has_calculator);
        CHECK("existe SymbolInfo Point Class src/math.h", has_point);
        CHECK("existe SymbolInfo main Function app.py", has_main);
        CHECK("existe SymbolInfo App Class app.py", has_app_class);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_symbols: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_symbols: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_dependencies()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        ProjectContext ctx = ce.build();

        bool has_math_h_dep = false;
        bool has_vector_dep = false;
        bool has_os_dep = false;
        bool has_datetime_dep = false;

        for (const auto& dep : ctx.dependencies)
        {
            if (dep.from_file == "src/math.cpp" && dep.target == "math.h" && dep.kind == "include" && dep.external == false)
            {
                has_math_h_dep = true;
            }
            else if (dep.from_file == "src/math.cpp" && dep.target == "vector" && dep.kind == "include" && dep.external == true)
            {
                has_vector_dep = true;
            }
            else if (dep.from_file == "app.py" && dep.target == "os" && dep.kind == "import" && dep.external == true)
            {
                has_os_dep = true;
            }
            else if (dep.from_file == "app.py" && dep.target == "datetime" && dep.kind == "import" && dep.external == true)
            {
                has_datetime_dep = true;
            }
        }

        CHECK("dep math.cpp -> math.h include external=false", has_math_h_dep);
        CHECK("dep math.cpp -> vector include external=true", has_vector_dep);
        CHECK("dep app.py -> os import external=true", has_os_dep);
        CHECK("dep app.py -> datetime import external=true", has_datetime_dep);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_dependencies: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_dependencies: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_symbol_line_and_signature()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        ProjectContext ctx = ce.build();

        for (const auto& file : ctx.files)
        {
            for (const auto& sym : file.symbols)
            {
                CHECK("todos simbolos line >= 1", sym.line >= 1);
                CHECK("todos simbolos signature no vacia", !sym.signature.empty());
            }
        }
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_symbol_line_and_signature: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_symbol_line_and_signature: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_set_ignore_dirs()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce2(tmp_dir);
        ce2.set_ignore_dirs({"src"});
        ProjectContext ctx2 = ce2.build();

        CHECK("set_ignore_dirs({src}) -> total_files == 2 (app.py + build/obj.cpp)", ctx2.total_files == 2);
    bool any_src = false;
    for (const auto& f : ctx2.files)
    {
        if (f.path.find("src") != std::string::npos)
        {
            any_src = true;
            break;
        }
    }
    CHECK("set_ignore_dirs({src}) -> ningun archivo de src", !any_src);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_set_ignore_dirs: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_set_ignore_dirs: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_ignore_dirs_default()
{
    auto tmp_dir = create_test_project();

    struct Cleanup {
        std::string path;
        ~Cleanup() { std::filesystem::remove_all(path); }
    } cleanup{tmp_dir};

    try
    {
        ContextEngine ce(tmp_dir);
        auto dirs = ce.ignore_dirs();

        bool has_git = false;
        bool has_build = false;

        for (const auto& d : dirs)
        {
            if (d == ".git") has_git = true;
            if (d == "build") has_build = true;
        }

        CHECK("ignore_dirs() contiene .git", has_git);
        CHECK("ignore_dirs() contiene build", has_build);
    }
    catch (const std::regex_error& e)
    {
        std::cout << "FAILED: test_ignore_dirs_default: regex_error: " << e.what() << "\n";
        ++g_failed;
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_ignore_dirs_default: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

int main()
{
    test_build_basic();
    test_files_info();
    test_symbols();
    test_dependencies();
    test_symbol_line_and_signature();
    test_set_ignore_dirs();
    test_ignore_dirs_default();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}