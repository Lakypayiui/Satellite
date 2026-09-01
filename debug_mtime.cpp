#include <iostream>
#include <filesystem>
#include <fstream>
#include <chrono>
#include <thread>
#include <cstdint>

namespace fs = std::filesystem;

int main()
{
    auto tmp_base = fs::temp_directory_path();
    auto timestamp = std::chrono::system_clock::now().time_since_epoch().count();
    auto tmp_dir = tmp_base / ("satellite_dbg_" + std::to_string(timestamp));
    fs::create_directories(tmp_dir / "src");

    {
        std::ofstream f(tmp_dir / "src" / "f1.cpp");
        f << "int foo() { return 1; }\n";
    }

    auto ft1 = fs::last_write_time(tmp_dir / "src" / "f1.cpp");
    auto mtime1 = static_cast<std::int64_t>(ft1.time_since_epoch().count());
    std::cout << "mtime after create: " << mtime1 << std::endl;

    {
        std::ifstream ifs(tmp_dir / "src" / "f1.cpp", std::ios::binary | std::ios::ate);
        ifs.seekg(0);
        std::string content((std::istreambuf_iterator<char>(ifs)), std::istreambuf_iterator<char>());
    }

    auto ft2 = fs::last_write_time(tmp_dir / "src" / "f1.cpp");
    auto mtime2 = static_cast<std::int64_t>(ft2.time_since_epoch().count());
    std::cout << "mtime after ifstream: " << mtime2 << std::endl;

    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    auto ft3 = fs::last_write_time(tmp_dir / "src" / "f1.cpp");
    auto mtime3 = static_cast<std::int64_t>(ft3.time_since_epoch().count());
    std::cout << "mtime after sleep(100ms): " << mtime3 << std::endl;

    fs::remove_all(tmp_dir);
    return 0;
}
