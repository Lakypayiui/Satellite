// Tests de invalidación de contexto (ETAPA 2c — REINTENTO)
// Técnica correcta de mtime con file_time_type y comparación ==

#include <iostream>
#include <string>
#include <vector>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <thread>

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

std::filesystem::path create_test_project()
{
    auto tmp_base = std::filesystem::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_inv_" + std::to_string(timestamp));

    std::filesystem::create_directories(tmp_dir / "src");

    {
        std::ofstream f(tmp_dir / "src" / "file1.cpp");
        f << "int foo() { return 0; }\n";
    }

    {
        std::ofstream f(tmp_dir / "src" / "file2.cpp");
        f << "int bar() { return 0; }\n";
    }

    return tmp_dir;
}

void cleanup_temp_dir(const std::filesystem::path& path)
{
    try
    {
        std::filesystem::remove_all(path);
    }
    catch (const std::filesystem::filesystem_error&)
    {
    }
}

void test_changed_paths_empty()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        auto changes = builder.changed_paths(idx, tmp_dir);
        CHECK("TEST1: changed_paths vacío", changes.empty());
        CHECK("TEST1: is_stale == false", !builder.is_stale(idx, tmp_dir));
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST1: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_changed_paths_modified_file()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        {
            // Fijar el mtime explícitamente: reescribir el archivo no garantiza
            // que last_write_time cambie dentro de la resolución de Windows/NTFS.
            const auto ruta = std::filesystem::path(tmp_dir) / "src" / "file1.cpp";
            const auto nuevo_mtime =
                std::filesystem::last_write_time(ruta) + std::chrono::seconds(2);
            std::filesystem::last_write_time(ruta, nuevo_mtime);
        }

        auto changes = builder.changed_paths(idx, tmp_dir);
        CHECK("TEST2: changed_paths size == 1", changes.size() == 1);
        CHECK("TEST2: changed_paths[0] == src/file1.cpp",
              changes.size() == 1 && changes[0] == "src/file1.cpp");
        CHECK("TEST2: is_stale == true", builder.is_stale(idx, tmp_dir));
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST2: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_changed_paths_new_file()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        {
            std::ofstream f(tmp_dir / "src" / "file3.cpp");
            f << "int baz() { return 0; }\n";
        }

        auto changes = builder.changed_paths(idx, tmp_dir);
        bool has_file3 = false;
        for (const auto& p : changes)
        {
            if (p == "src/file3.cpp") has_file3 = true;
        }
        CHECK("TEST3: changed_paths contiene src/file3.cpp", has_file3);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST3: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_changed_paths_deleted_file()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        std::filesystem::remove(tmp_dir / "src" / "file2.cpp");

        auto changes = builder.changed_paths(idx, tmp_dir);
        bool has_file2 = false;
        for (const auto& p : changes)
        {
            if (p == "src/file2.cpp") has_file2 = true;
        }
        CHECK("TEST4: changed_paths contiene src/file2.cpp", has_file2);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST4: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_mtime_roundtrip_exact()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        ProjectIndex loaded = load(std::filesystem::path(tmp_dir) / "index.json");

        auto orig_ft = std::filesystem::file_time_type(
            std::filesystem::file_time_type::duration(idx.files[0].mtime));
        auto loaded_ft = std::filesystem::file_time_type(
            std::filesystem::file_time_type::duration(loaded.files[0].mtime));
        CHECK("TEST5: mtime roundtrip exacto (file_time_type ==)", orig_ft == loaded_ft);
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST5: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_changed_paths_no_false_positives_after_roundtrip()
{
    auto tmp_dir = create_test_project();
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        save(idx, std::filesystem::path(tmp_dir) / "index.json");

        ProjectIndex loaded = load(std::filesystem::path(tmp_dir) / "index.json");

        auto changes = builder.changed_paths(loaded, tmp_dir);
        CHECK("TEST6: changed_paths(loaded, root) vacío", changes.empty());
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: TEST6: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

int main()
{
    test_changed_paths_empty();
    test_changed_paths_modified_file();
    test_changed_paths_new_file();
    test_changed_paths_deleted_file();
    test_mtime_roundtrip_exact();
    test_changed_paths_no_false_positives_after_roundtrip();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}