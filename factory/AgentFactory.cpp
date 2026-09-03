#include "AgentFactory.h"

#include <json.hpp>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <cstdlib>
#include <cstdio>
#include <memory>
#include <array>

#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace satellite::factory
{

AgentFactory::AgentFactory(AgentRegistry& registry, std::filesystem::path work_dir, std::filesystem::path framework_root, std::string compiler)
    : registry_(registry)
    , work_dir_(std::move(work_dir))
    , framework_root_(std::move(framework_root))
    , compiler_(std::move(compiler))
{
    std::filesystem::create_directories(work_dir_);
}

AgentFactory::~AgentFactory()
{
    unload_all();
}

std::string AgentFactory::get_library_path(AgentID id) const
{
#ifdef _WIN32
    return (work_dir_ / ("agent_" + std::to_string(id) + ".dll")).string();
#else
    return (work_dir_ / ("libagent_" + std::to_string(id) + ".so")).string();
#endif
}

std::string AgentFactory::get_test_executable_path(AgentID id) const
{
#ifdef _WIN32
    return (work_dir_ / ("test_agent_" + std::to_string(id) + ".exe")).string();
#else
    return (work_dir_ / ("test_agent_" + std::to_string(id))).string();
#endif
}

bool AgentFactory::compile(const std::vector<std::string>& sources, const std::vector<std::string>& extra_flags, std::string& output, std::string& error) const
{
    std::ostringstream cmd;
    cmd << compiler_ << " -std=c++17";

    for (const auto& flag : extra_flags)
    {
        cmd << " " << flag;
    }

    cmd << " -I\"" << framework_root_.string() << "\"";
    cmd << " -I\"" << (framework_root_ / "third_party").string() << "\"";
    cmd << " -I\"" << (framework_root_ / "third_party" / "json").string() << "\"";

    for (const auto& src : sources)
    {
        cmd << " \"" << src << "\"";
    }

    cmd << " 2>&1";

    std::array<char, 128> buffer;
    std::string result;
    std::unique_ptr<FILE, decltype(&pclose)> pipe(popen(cmd.str().c_str(), "r"), pclose);
    if (!pipe)
    {
        error = "Failed to run compiler";
        return false;
    }
    while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr)
    {
        result += buffer.data();
    }

    output = result;
    int status = pclose(pipe.release());
    if (status != 0)
    {
        error = result;
        return false;
    }
    return true;
}

FactoryResult AgentFactory::create_agent(const AgentSpec& spec)
{
    FactoryResult result;
    result.agent_id = spec.id;

    if (spec.id == UNKNOWN_AGENT_ID)
    {
        result.stage = "validate";
        result.message = "Agent ID cannot be UNKNOWN_AGENT_ID (0)";
        return result;
    }
    if (spec.name.empty())
    {
        result.stage = "validate";
        result.message = "Agent name cannot be empty";
        return result;
    }
    if (spec.implementation_code.empty())
    {
        result.stage = "validate";
        result.message = "Implementation code cannot be empty";
        return result;
    }
    if (spec.capabilities.empty())
    {
        result.stage = "validate";
        result.message = "Capabilities cannot be empty";
        return result;
    }
    if (!spec.input_schema.is_object())
    {
        result.stage = "validate";
        result.message = "input_schema must be a JSON object";
        return result;
    }
    if (!spec.output_schema.is_object())
    {
        result.stage = "validate";
        result.message = "output_schema must be a JSON object";
        return result;
    }

    std::string agent_cpp = (work_dir_ / ("agent_" + std::to_string(spec.id) + ".cpp")).string();
    std::string test_cpp = (work_dir_ / ("test_agent_" + std::to_string(spec.id) + ".cpp")).string();

    {
        std::ofstream ofs(agent_cpp);
        if (!ofs)
        {
            result.stage = "validate";
            result.message = "Failed to write agent source file";
            return result;
        }
        ofs << spec.implementation_code;
    }

    {
        nlohmann::json test_cases_json = nlohmann::json::array();
        for (const auto& tc : spec.test_cases)
        {
            test_cases_json.push_back({{"input", tc.first}, {"expected", tc.second}});
        }
        std::string test_cases_str = test_cases_json.dump();

        std::string harness = R"HARNESS(
#include "core/agent/AgentRequest.h"
#include "core/agent/AgentResult.h"
#include "core/agent/IAgent.h"
#include "factory/AgentPlugin.h"
#include <json.hpp>
#include <iostream>
#include <cmath>

int main()
{
    using json = nlohmann::json;
    using satellite::core::agent::AgentRequest;
    using satellite::core::agent::AgentResult;
    using satellite::core::agent::AgentStatus;
    using satellite::core::agent::AgentID;

    satellite::core::agent::IAgent* agent = satellite_create_agent();
    if (!agent)
    {
        std::cerr << "FAILED: satellite_create_agent returned null" << std::endl;
        return 1;
    }

    const json TEST_CASES = json::parse()HARNESS";

        harness += "R\"JSON(" + test_cases_str + ")JSON\");\n";

        harness += R"HARNESS(

    int passed = 0;
    int failed = 0;
    int test_index = 0;

    for (const auto& tc : TEST_CASES)
    {
        json input = tc["input"];
        json expected = tc["expected"];

        AgentRequest req;
        req.agent_id = )HARNESS";
        harness += std::to_string(spec.id) + ";\n";
        harness += R"HARNESS(
        req.input = input;
        req.context = json::object();
        req.metadata = json::object();

        AgentResult result = agent->execute(req);

        bool match = true;
        if (result.output.is_number() && expected.is_number())
        {
            double diff = std::abs(result.output.get<double>() - expected.get<double>());
            if (diff > 1e-9)
                match = false;
        }
        else if (result.output.type() != expected.type())
        {
            match = false;
        }
        else
        {
            match = (result.output == expected);
        }

        if (match)
        {
            std::cout << "PASSED " << test_index << std::endl;
            passed++;
        }
        else
        {
            std::cout << "FAILED " << test_index << std::endl;
            std::cout << "  Expected: " << expected.dump() << std::endl;
            std::cout << "  Got:      " << result.output.dump() << std::endl;
            failed++;
        }
        test_index++;
    }

    satellite_destroy_agent(agent);
    std::cout << passed << " passed, " << failed << " failed" << std::endl;
    return (failed == 0) ? 0 : 1;
}
)HARNESS";

        std::ofstream ofs(test_cpp);
        if (!ofs)
        {
            result.stage = "validate";
            result.message = "Failed to write test harness file";
            return result;
        }
        ofs << harness;
    }

    std::string compile_output, compile_error;
    std::vector<std::string> test_sources = {agent_cpp, test_cpp};
    std::vector<std::string> compile_flags = {"-O2", "-o", get_test_executable_path(spec.id)};

    if (!compile(test_sources, compile_flags, compile_output, compile_error))
    {
        result.stage = "compile_test";
        result.message = compile_error.empty() ? compile_output : compile_error;
        return result;
    }

    std::string test_exe = get_test_executable_path(spec.id);
    std::string run_output;
    {
        std::array<char, 128> buffer;
        std::string result_str;
        std::unique_ptr<FILE, decltype(&pclose)> pipe(popen((test_exe + " 2>&1").c_str(), "r"), pclose);
        if (!pipe)
        {
            result.stage = "run_tests";
            result.message = "Failed to run test executable";
            return result;
        }
        while (fgets(buffer.data(), buffer.size(), pipe.get()) != nullptr)
        {
            result_str += buffer.data();
        }
        run_output = result_str;
        int status = pclose(pipe.release());
        if (status != 0)
        {
            result.stage = "run_tests";
            result.message = run_output;
            return result;
        }
    }

    std::vector<std::string> lib_sources = {agent_cpp};
    std::vector<std::string> lib_flags = {"-shared", "-fPIC", "-O2", "-o", get_library_path(spec.id)};

    std::string lib_output, lib_error;
    if (!compile(lib_sources, lib_flags, lib_output, lib_error))
    {
        result.stage = "compile_lib";
        result.message = lib_error.empty() ? lib_output : lib_error;
        return result;
    }

    std::string lib_path = get_library_path(spec.id);
    void* handle = nullptr;
#ifdef _WIN32
    std::wstring wlib_path(lib_path.begin(), lib_path.end());
    handle = LoadLibraryW(wlib_path.c_str());
    if (!handle)
    {
        result.stage = "load_lib";
        result.message = "LoadLibraryW failed: " + std::to_string(GetLastError());
        return result;
    }
#else
    handle = dlopen(lib_path.c_str(), RTLD_LAZY);
    if (!handle)
    {
        result.stage = "load_lib";
        result.message = std::string("dlopen failed: ") + dlerror();
        return result;
    }
#endif

    using CreateAgentFn = satellite::core::agent::IAgent* (*)();
    using DestroyAgentFn = void (*)(satellite::core::agent::IAgent*);
    CreateAgentFn create_fn = nullptr;
    DestroyAgentFn destroy_fn = nullptr;
#ifdef _WIN32
    create_fn = reinterpret_cast<CreateAgentFn>(reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(handle), "satellite_create_agent")));
    destroy_fn = reinterpret_cast<DestroyAgentFn>(reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(handle), "satellite_destroy_agent")));
#else
    create_fn = reinterpret_cast<CreateAgentFn>(dlsym(handle, "satellite_create_agent"));
    destroy_fn = reinterpret_cast<DestroyAgentFn>(dlsym(handle, "satellite_destroy_agent"));
#endif

    if (!create_fn || !destroy_fn)
    {
        result.stage = "load_lib";
        result.message = "Failed to resolve plugin create/destroy symbols";
#ifdef _WIN32
        FreeLibrary(static_cast<HMODULE>(handle));
#else
        dlclose(handle);
#endif
        return result;
    }

    satellite::core::agent::IAgent* agent_instance = create_fn();
    if (!agent_instance)
    {
        result.stage = "load_lib";
        result.message = "satellite_create_agent returned null";
#ifdef _WIN32
        FreeLibrary(static_cast<HMODULE>(handle));
#else
        dlclose(handle);
#endif
        return result;
    }

    AgentDescriptor desc;
    desc.id = spec.id;
    desc.name = spec.name;
    desc.description = spec.description;
    desc.version = spec.version;
    desc.input_schema = spec.input_schema;
    desc.output_schema = spec.output_schema;
    desc.context_requirements = spec.context_requirements;
    desc.capabilities = spec.capabilities;
    desc.agent = agent_instance;

    if (!registry_.register_agent(desc))
    {
        result.stage = "register";
        result.message = "Duplicate agent ID or registration failed";
    destroy_fn(agent_instance);
#ifdef _WIN32
        FreeLibrary(static_cast<HMODULE>(handle));
#else
    destroy_fn(agent_instance);
        dlclose(handle);
#endif
        return result;
    }

    loaded_libs_[spec.id] = LoadedLibrary{handle, agent_instance, destroy_fn};

    result.ok = true;
    result.stage = "ok";
    result.message = "";
    return result;
}

bool AgentFactory::release_agent(AgentID id)
{
    auto it = loaded_libs_.find(id);
    if (it == loaded_libs_.end())
        return false;

    const LoadedLibrary loaded = it->second;
    registry_.unregister_agent(id);
    loaded.destroy(loaded.agent);
#ifdef _WIN32
    FreeLibrary(static_cast<HMODULE>(loaded.handle));
#else
    dlclose(loaded.handle);
#endif
    loaded_libs_.erase(it);
    return true;
}

void AgentFactory::unload_all()
{
    for (auto& [id, loaded] : loaded_libs_)
    {
        registry_.unregister_agent(id);
        loaded.destroy(loaded.agent);
#ifdef _WIN32
        FreeLibrary(static_cast<HMODULE>(loaded.handle));
#else
        dlclose(loaded.handle);
#endif
    }
    loaded_libs_.clear();
}

void AgentFactory::cleanup()
{
    unload_all();
    if (std::filesystem::exists(work_dir_))
    {
        for (const auto& entry : std::filesystem::directory_iterator(work_dir_))
        {
            std::filesystem::remove(entry.path());
        }
    }
}

} // namespace satellite::factory