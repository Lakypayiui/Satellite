from satellite_py.registry import AgentDescriptor
from satellite_py.security import SecurityPolicy


def test_security_denies_by_default():
    policy = SecurityPolicy()
    allowed, denied = policy.validate_agent(AgentDescriptor(id=1, capabilities=["process.execute"]))
    assert not allowed
    assert denied == "process.execute"


def test_security_defaults_and_config():
    policy = SecurityPolicy()
    policy.load_defaults()
    assert policy.is_allowed("filesystem.read")
    assert not policy.is_allowed("network.request")
    policy.from_config({"network.request": True})
    assert policy.is_allowed("network.request")
    assert not policy.is_allowed("filesystem.read")


def test_empty_capabilities_require_explicit_rule():
    descriptor = AgentDescriptor(id=1)
    policy = SecurityPolicy()
    assert policy.validate_agent(descriptor) == (False, "agent.no_capabilities")
    policy.set_allowed("agent.no_capabilities", True)
    assert policy.validate_agent(descriptor) == (True, None)
