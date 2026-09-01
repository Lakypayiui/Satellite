// Mini framework de test para context::ProjectIndex (ETAPA 2)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <chrono>

#include "context/engine/ProjectIndex.h"

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

std::string create_test_project_for_index()
{
    auto tmp_base = std::filesystem::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_test_idx_" + std::to_string(timestamp));

    std::filesystem::create_directories(tmp_dir / "src");

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
        std::ofstream f(tmp_dir / "src" / "utils.cpp");
        f << "#include <string>\n\n";
        f << "void trim(std::string& s) { }\n\n";
        f << "class Helper {\n";
        f << "public:\n";
        f << "    static std::string join(const std::string& a, const std::string& b) { return a + b; }\n";
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

    return tmp_dir.string();
}

void cleanup_temp_dir(const std::string& path)
{
    try
    {
        std::filesystem::remove_all(path);
    }
    catch (const std::filesystem::filesystem_error&)
    {
    }
}

void test_build_basic()
{
    auto tmp_dir = create_test_project_for_index();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        CHECK("idx.total_files == 3", idx.total_files == 3);
        CHECK("idx.total_lines > 0", idx.total_lines > 0);

        bool has_math_cpp = false;
        bool has_utils_cpp = false;
        bool has_app_py = false;

        for (const auto& f : idx.files)
        {
            if (f.path == "src/math.cpp")
            {
                has_math_cpp = true;
                CHECK("math.cpp path no vacio", !f.path.empty());
                CHECK("math.cpp language == C++", f.language == "C++");
                CHECK("math.cpp symbols >= 1", f.symbols.size() >= 1);
                CHECK("math.cpp dependencies >= 1", f.dependencies.size() >= 1);
                CHECK("math.cpp mtime != 0 (extraído)", f.mtime != 0);
            }
            else if (f.path == "src/utils.cpp")
            {
                has_utils_cpp = true;
                CHECK("utils.cpp path no vacio", !f.path.empty());
                CHECK("utils.cpp language == C++", f.language == "C++");
                CHECK("utils.cpp symbols >= 1", f.symbols.size() >= 1);
                CHECK("utils.cpp dependencies >= 1", f.dependencies.size() >= 1);
                CHECK("utils.cpp mtime != 0 (extraído)", f.mtime != 0);
            }
            else if (f.path == "app.py")
            {
                has_app_py = true;
                CHECK("app.py path no vacio", !f.path.empty());
                CHECK("app.py language == Python", f.language == "Python");
                CHECK("app.py symbols >= 1", f.symbols.size() >= 1);
                CHECK("app.py dependencies >= 1", f.dependencies.size() >= 1);
                CHECK("app.py mtime != 0 (extraído)", f.mtime != 0);
            }
        }

        CHECK("existe src/math.cpp", has_math_cpp);
        CHECK("existe src/utils.cpp", has_utils_cpp);
        CHECK("existe app.py", has_app_py);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_basic: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_roundtrip()
{
    auto tmp_dir = create_test_project_for_index();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_roundtrip_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        ProjectIndex loaded = load(tmp_file);

        CHECK("roundtrip root coincide", loaded.root == idx.root);
        CHECK("roundtrip total_files coincide", loaded.total_files == idx.total_files);
        CHECK("roundtrip total_lines coincide", loaded.total_lines == idx.total_lines);
        CHECK("roundtrip files.size() coincide", loaded.files.size() == idx.files.size());

        if (!idx.files.empty() && !loaded.files.empty())
        {
            const auto& fi = idx.files[0];
            const auto& li = loaded.files[0];
            CHECK("roundtrip primer archivo path coincide", fi.path == li.path);
            CHECK("roundtrip primer archivo language coincide", fi.language == li.language);
            CHECK("roundtrip primer archivo lines coincide", fi.lines == li.lines);
            CHECK("roundtrip primer archivo symbols.size() coincide", fi.symbols.size() == li.symbols.size());
            CHECK("roundtrip primer archivo dependencies.size() coincide", fi.dependencies.size() == li.dependencies.size());
        }

        try
        {
            std::filesystem::remove(tmp_file);
        }
        catch (const std::filesystem::filesystem_error&)
        {
        }
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_roundtrip: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_load_missing_file()
{
    try
    {
        ProjectIndex idx = load("/tmp/nonexistent_project_index_12345.json");
        (void)idx;
        std::cout << "FAILED: load inexistente no lanzo excepcion\n";
        ++g_failed;
    }
    catch (const std::runtime_error&)
    {
        CHECK("load archivo inexistente lanza runtime_error", true);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: load archivo inexistente tipo wrong: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_mtime_positive()
{
    auto tmp_dir = create_test_project_for_index();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        bool all_mtime_extracted = true;
        for (const auto& f : idx.files)
        {
            // El count() nativo del file_clock puede ser negativo según el
            // toolchain (libstdc++ usa un epoch futuro): lo importante es que
            // se extrajo (distinto de 0) y que el roundtrip sea exacto.
            if (f.mtime == 0)
            {
                all_mtime_extracted = false;
            }
        }
        CHECK("todos los mtime extraídos (!= 0)", all_mtime_extracted);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_mtime_positive: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

int main()
{
    test_build_basic();
    test_roundtrip();
    test_load_missing_file();
    test_mtime_positive();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}