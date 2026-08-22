// Mini framework de test para context::adapter (FASE 10)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <memory>

#include "context/adapter/ProjectAdapter.h"

using namespace satellite::context;
using namespace std::filesystem;

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

path create_temp_dir(const std::string& prefix)
{
    path tmp = temp_directory_path() / (prefix + "_" + std::to_string(std::chrono::system_clock::now().time_since_epoch().count()));
    create_directories(tmp);
    return tmp;
}

void write_file(const path& file_path, const std::string& content)
{
    std::ofstream ofs(file_path);
    ofs << content;
    ofs.close();
}

void remove_all_quiet(const path& p)
{
    try { remove_all(p); } catch (...) {}
}

void test_cpp_project_adapter()
{
    path cpp_proj = create_temp_dir("cpp_proj");

    // CMakeLists.txt
    write_file(cpp_proj / "CMakeLists.txt",
        "cmake_minimum_required(VERSION 3.10)\nproject(Calc)\nadd_executable(calc src/calc.cpp)\n");

    // src/calc.cpp
    create_directories(cpp_proj / "src");
    write_file(cpp_proj / "src" / "calc.cpp",
        "#include \"calc.h\"\n\n"
        "int factorial(int n) {\n"
        "    if (n <= 1) return 1;\n"
        "    return n * factorial(n - 1);\n"
        "}\n\n"
        "int Calc::add(int a, int b) { return a + b; }\n");

    // src/calc.h
    write_file(cpp_proj / "src" / "calc.h",
        "#pragma once\n\n"
        "class Calc {\n"
        "public:\n"
        "    int add(int a, int b);\n"
        "};\n");

    // app.py (relleno - debe ser ignorado por CppProjectAdapter)
    write_file(cpp_proj / "app.py",
        "def main():\n    print('hola')\n");

    CppProjectAdapter a;

    CHECK("CppProjectAdapter supports cpp_proj", a.supports(cpp_proj));
    CHECK("CppProjectAdapter language == C++", a.language() == "C++");

    auto ctx = a.build_context(cpp_proj);
    CHECK("CppProjectAdapter build_context total_files == 2", ctx.total_files == 2);

    // Verificar que todos los archivos son language == "C++"
    bool all_cpp = true;
    for (const auto& file : ctx.files) {
        if (file.language != "C++") {
            all_cpp = false;
            break;
        }
    }
    CHECK("CppProjectAdapter build_context todos files language C++", all_cpp);

    // Verificar que NO hay archivos Python
    bool has_python = false;
    for (const auto& file : ctx.files) {
        if (file.language == "Python") {
            has_python = true;
            break;
        }
    }
    CHECK("CppProjectAdapter build_context sin archivos Python", !has_python);

    // Verificar símbolos: factorial o Calc
    bool has_factorial_or_calc = false;
    for (const auto& file : ctx.files) {
        for (const auto& sym : file.symbols) {
            if (sym.name == "factorial" || sym.name == "Calc") {
                has_factorial_or_calc = true;
                break;
            }
        }
        if (has_factorial_or_calc) break;
    }
    CHECK("CppProjectAdapter build_context tiene símbolo factorial o Calc", has_factorial_or_calc);

    remove_all_quiet(cpp_proj);
}

void test_python_project_adapter()
{
    path py_proj = create_temp_dir("py_proj");

    // app.py
    write_file(py_proj / "app.py",
        "def main():\n    pass\n\n"
        "class MyClass:\n    def method(self):\n        return 42\n");

    // requirements.txt
    write_file(py_proj / "requirements.txt",
        "requests\nnumpy\n");

    PythonProjectAdapter p;

    CHECK("PythonProjectAdapter supports py_proj", p.supports(py_proj));
    CHECK("PythonProjectAdapter language == Python", p.language() == "Python");

    auto ctx = p.build_context(py_proj);
    CHECK("PythonProjectAdapter build_context total_files == 1", ctx.total_files == 1);

    // Verificar que el archivo es language == "Python"
    bool all_python = true;
    for (const auto& file : ctx.files) {
        if (file.language != "Python") {
            all_python = false;
            break;
        }
    }
    CHECK("PythonProjectAdapter build_context todos files language Python", all_python);

    // Verificar símbolo "main"
    bool has_main = false;
    for (const auto& file : ctx.files) {
        for (const auto& sym : file.symbols) {
            if (sym.name == "main") {
                has_main = true;
                break;
            }
        }
        if (has_main) break;
    }
    CHECK("PythonProjectAdapter build_context tiene símbolo main", has_main);

    // CppProjectAdapter NO debe soportar py_proj
    CppProjectAdapter a;
    CHECK("CppProjectAdapter NO supports py_proj", !a.supports(py_proj));

    remove_all_quiet(py_proj);
}

void test_factory_detect()
{
    // mixed: main.cpp y script.py -> factory debe devolver CppProjectAdapter (orden C++ primero)
    path mixed = create_temp_dir("mixed");
    write_file(mixed / "main.cpp", "int main() { return 0; }\n");
    write_file(mixed / "script.py", "print('hola')\n");

    auto adapter = ProjectAdapterFactory::detect(mixed);
    CHECK("Factory detect mixed no es nullptr", adapter != nullptr);
    CHECK("Factory detect mixed language == C++ (orden C++ primero)", adapter->language() == "C++");

    remove_all_quiet(mixed);

    // py_proj solo Python -> factory devuelve PythonProjectAdapter
    path py_proj = create_temp_dir("py_proj");
    write_file(py_proj / "app.py", "def main(): pass\n");
    write_file(py_proj / "requirements.txt", "requests\n");

    adapter = ProjectAdapterFactory::detect(py_proj);
    CHECK("Factory detect py_proj no es nullptr", adapter != nullptr);
    CHECK("Factory detect py_proj language == Python", adapter->language() == "Python");

    remove_all_quiet(py_proj);

    // vacio: directorio sin archivos -> nullptr
    path vacio = create_temp_dir("vacio");
    adapter = ProjectAdapterFactory::detect(vacio);
    CHECK("Factory detect vacio es nullptr", adapter == nullptr);

    remove_all_quiet(vacio);
}

void test_polimorfismo()
{
    path cpp_proj = create_temp_dir("cpp_proj_poly");
    write_file(cpp_proj / "CMakeLists.txt", "project(Test)\n");
    write_file(cpp_proj / "main.cpp", "int main() { return 0; }\n");

    // Usar IProjectAdapter* polimórficamente con CppProjectAdapter
    std::unique_ptr<IProjectAdapter> adapter = std::make_unique<CppProjectAdapter>();

    CHECK("Polimorfismo IProjectAdapter* supports", adapter->supports(cpp_proj));
    CHECK("Polimorfismo IProjectAdapter* language", adapter->language() == "C++");

    auto ctx = adapter->build_context(cpp_proj);
    CHECK("Polimorfismo IProjectAdapter* build_context", ctx.total_files == 1);

    remove_all_quiet(cpp_proj);
}

int main()
{
    test_cpp_project_adapter();
    test_python_project_adapter();
    test_factory_detect();
    test_polimorfismo();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}