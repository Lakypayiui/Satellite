// Mini framework de test para context::SemanticContext (Etapa 2b)
// Sin dependencias externas: solo C++17 estándar

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <chrono>

#include "context/engine/ProjectIndex.h"
#include "context/engine/SemanticContext.h"

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

std::string create_test_project_for_semantic()
{
    auto tmp_base = std::filesystem::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_test_sc_" + std::to_string(timestamp));

    std::filesystem::create_directories(tmp_dir / "src");

    {
        std::ofstream f(tmp_dir / "src" / "uno.cpp");
        f << "// ARCHIVO_UNO\n";
        f << "int foo() { return 1; }\n";
    }

    {
        std::ofstream f(tmp_dir / "src" / "dos.cpp");
        f << "// ARCHIVO_DOS\n";
        f << "int bar() { return 2; }\n";
    }

    {
        std::ofstream f(tmp_dir / "app.py");
        f << "# script\n";
        f << "print('hola')\n";
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

void test_build_for_paths_basic()
{
    auto tmp_dir = create_test_project_for_semantic();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        IncrementalContextBuilder builder_sc(tmp_dir);

        std::vector<std::string> paths = {"src/uno.cpp", "src/dos.cpp"};
        SemanticContext ctx = builder_sc.build_for_paths(idx, paths);

        CHECK("files.size()==2", ctx.files.size() == 2);
        CHECK("total_chars == suma contenidos",
              ctx.total_chars == ctx.files[0].content.size() + ctx.files[1].content.size());

        bool found_uno = false;
        bool found_dos = false;
        for (const auto& f : ctx.files)
        {
            if (f.path == "src/uno.cpp")
            {
                found_uno = true;
                CHECK("uno.cpp contiene marca", f.content.find("// ARCHIVO_UNO") != std::string::npos);
                CHECK("uno.cpp language==C++", f.language == "C++");
            }
            else if (f.path == "src/dos.cpp")
            {
                found_dos = true;
                CHECK("dos.cpp contiene marca", f.content.find("// ARCHIVO_DOS") != std::string::npos);
                CHECK("dos.cpp language==C++", f.language == "C++");
            }
        }
        CHECK("se encontro uno.cpp", found_uno);
        CHECK("se encontro dos.cpp", found_dos);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_for_paths_basic: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_build_for_paths_ignore_missing()
{
    auto tmp_dir = create_test_project_for_semantic();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        IncrementalContextBuilder builder_sc(tmp_dir);

        std::vector<std::string> paths = {"src/uno.cpp", "no_existe.cpp"};
        SemanticContext ctx = builder_sc.build_for_paths(idx, paths);

        CHECK("files.size()==1", ctx.files.size() == 1);
        CHECK("solo existe uno.cpp",
              ctx.files.size() == 1 && ctx.files[0].path == "src/uno.cpp");
        CHECK("uno.cpp contenido correcto",
              ctx.files[0].content.find("// ARCHIVO_UNO") != std::string::npos);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_for_paths_ignore_missing: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_build_for_paths_max_chars()
{
    auto tmp_dir = create_test_project_for_semantic();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        IncrementalContextBuilder builder_sc(tmp_dir);

        std::size_t uno_size = 0;
        {
            const auto full_path = tmp_dir + "/src/uno.cpp";
            std::ifstream ifs(full_path, std::ios::binary | std::ios::ate);
            if (ifs) uno_size = static_cast<std::size_t>(ifs.tellg());
        }

        std::vector<std::string> paths = {"src/uno.cpp", "src/dos.cpp"};
        SemanticContext ctx = builder_sc.build_for_paths(idx, paths, uno_size + 1);

        CHECK("files.size()==1 con limite ajustado", ctx.files.size() == 1);
        CHECK("solo cabio uno.cpp", ctx.files[0].path == "src/uno.cpp");
        CHECK("total_chars == size(uno.cpp)", ctx.total_chars == uno_size);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_for_paths_max_chars: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_build_for_paths_empty()
{
    auto tmp_dir = create_test_project_for_semantic();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        IncrementalContextBuilder builder_sc(tmp_dir);

        SemanticContext ctx = builder_sc.build_for_paths(idx, {});

        CHECK("archivos vacios con paths vacio", ctx.files.empty());
        CHECK("total_chars==0 con paths vacio", ctx.total_chars == 0);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_for_paths_empty: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_build_for_paths_not_in_index()
{
    auto tmp_dir = create_test_project_for_semantic();

    struct Cleanup {
        std::string path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        IncrementalContextBuilder builder_sc(tmp_dir);

        const std::string disco_path = "src/que_no_existe_en_index.cpp";
        std::filesystem::path disco_full_path = std::filesystem::path(tmp_dir) / disco_path;
        std::ofstream f(disco_full_path);
        f << "// Este archivo existe en disco pero no en el indice\n";
        f.close();

        std::vector<std::string> paths = {disco_path};
        SemanticContext ctx = builder_sc.build_for_paths(idx, paths);

        CHECK("archivo en disco pero no en indice se ignora", ctx.files.empty());
        CHECK("total_chars==0 cuando archivo ignorado", ctx.total_chars == 0);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_build_for_paths_not_in_index: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

int main()
{
    test_build_for_paths_basic();
    test_build_for_paths_ignore_missing();
    test_build_for_paths_max_chars();
    test_build_for_paths_empty();
    test_build_for_paths_not_in_index();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}