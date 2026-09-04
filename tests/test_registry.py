from satellite_py.registry import AgentDescriptor, AgentRegistry


def descriptor(agent_id: int) -> AgentDescriptor:
    return AgentDescriptor(id=agent_id, name=f"agent-{agent_id}")


def test_registry_rejects_zero_and_duplicates():
    registry = AgentRegistry()
    assert not registry.register_agent(descriptor(0))
    assert registry.register_agent(descriptor(2))
    assert not registry.register_agent(descriptor(2))


def test_registry_finds_and_lists_by_id():
    registry = AgentRegistry()
    registry.register_agent(descriptor(3))
    registry.register_agent(descriptor(1))
    assert registry.find_agent(3).name == "agent-3"
    assert registry.find_agent(99) is None
    assert [agent.id for agent in registry.list_agents()] == [1, 3]
