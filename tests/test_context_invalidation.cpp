// Mini framework de test para context::ProjectIndex invalidación (ETAPA 2c)
// Sin dependencias externas: solo C++17 estándar

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

std::filesystem::path create_test_project_for_invalidation()
{
    auto tmp_base = std::filesystem::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_inv_" + std::to_string(timestamp));

    std::filesystem::create_directories(tmp_dir / "src");

    {
        std::ofstream f(tmp_dir / "src" / "file1.cpp");
        f << "int foo() { return 1; }\n";
    }

    {
        std::ofstream f(tmp_dir / "src" / "file2.cpp");
        f << "int bar() { return 2; }\n";
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

void test_fresh_no_changes()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto changed = builder.changed_paths(idx, tmp_dir);
        CHECK("test_fresh_no_changes changed_paths vacio", changed.empty());
        CHECK("test_fresh_no_changes is_stale == false", !builder.is_stale(idx, tmp_dir));
    }
    catch (const std::exception& e)
    {
        std::cout << "FAILED: test_fresh_no_changes: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_file_modified()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_inv_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        std::this_thread::sleep_for(std::chrono::milliseconds(50));

        {
            std::ofstream f(tmp_dir / "src" / "file1.cpp");
            f << "int foo() { return 99; }\n";
        }

        ProjectIndex loaded = load(tmp_file);
        auto changed = builder.changed_paths(loaded, tmp_dir);

        bool found_file1 = false;
        for (const auto& p : changed)
        {
            if (p == "src/file1.cpp") found_file1 = true;
        }
        CHECK("test_file_modified changed_paths contiene src/file1.cpp", found_file1);
        CHECK("test_file_modified changed_paths.size() == 1", changed.size() == 1);
        CHECK("test_file_modified is_stale == true", builder.is_stale(loaded, tmp_dir));

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
        std::cout << "FAILED: test_file_modified: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_new_file()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_inv2_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        {
            std::ofstream f(tmp_dir / "src" / "file3.cpp");
            f << "int new_func() { return 3; }\n";
        }

        ProjectIndex loaded = load(tmp_file);
        auto changed = builder.changed_paths(loaded, tmp_dir);

        bool found_file3 = false;
        for (const auto& p : changed)
        {
            if (p == "src/file3.cpp") found_file3 = true;
        }
        CHECK("test_new_file changed_paths contiene src/file3.cpp", found_file3);
        CHECK("test_new_file is_stale == true", builder.is_stale(loaded, tmp_dir));

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
        std::cout << "FAILED: test_new_file: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_deleted_file()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_inv3_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        try
        {
            std::filesystem::remove(tmp_dir / "src" / "file2.cpp");
        }
        catch (const std::filesystem::filesystem_error&)
        {
        }

        ProjectIndex loaded = load(tmp_file);
        auto changed = builder.changed_paths(loaded, tmp_dir);

        bool found_file2 = false;
        for (const auto& p : changed)
        {
            if (p == "src/file2.cpp") found_file2 = true;
        }
        CHECK("test_deleted_file changed_paths contiene src/file2.cpp", found_file2);
        CHECK("test_deleted_file is_stale == true", builder.is_stale(loaded, tmp_dir));

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
        std::cout << "FAILED: test_deleted_file: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_roundtrip_mtime_identical()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_inv_rt_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        ProjectIndex loaded = load(tmp_file);

        if (!idx.files.empty() && !loaded.files.empty())
        {
            CHECK("test_roundtrip_mtime_identical mtime del primer archivo identico",
                  idx.files[0].mtime == loaded.files[0].mtime);
        }
        else
        {
            CHECK("test_roundtrip_mtime_identical archivos disponibles", false);
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
        std::cout << "FAILED: test_roundtrip_mtime_identical: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

void test_roundtrip_no_falsos_cambios()
{
    auto tmp_dir = create_test_project_for_invalidation();

    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup() { cleanup_temp_dir(path); }
    } cleanup{tmp_dir};

    try
    {
        ProjectIndexBuilder builder(tmp_dir);
        ProjectIndex idx = builder.build();

        auto tmp_file = std::filesystem::temp_directory_path() / ("idx_inv_rt2_" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()) + ".json");

        save(idx, tmp_file);

        ProjectIndex loaded = load(tmp_file);
        auto changed = builder.changed_paths(loaded, tmp_dir);

        CHECK("test_roundtrip_no_falsos_cambios changed_paths vacio", changed.empty());
        CHECK("test_roundtrip_no_falsos_cambios is_stale == false", !builder.is_stale(loaded, tmp_dir));

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
        std::cout << "FAILED: test_roundtrip_no_falsos_cambios: exception: " << e.what() << "\n";
        ++g_failed;
    }
}

int main()
{
    test_fresh_no_changes();
    test_file_modified();
    test_new_file();
    test_deleted_file();
    test_roundtrip_mtime_identical();
    test_roundtrip_no_falsos_cambios();

    std::cout << g_passed << " passed, " << g_failed << " failed\n";
    return g_failed == 0 ? 0 : 1;
}