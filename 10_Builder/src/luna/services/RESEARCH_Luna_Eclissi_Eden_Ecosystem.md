# Luna × Eclissi × Eden Ecosystem Research

## Research Document for Architectural Exploration

**Author:** Luna Engine Team
**Date:** 2025-02-05
**Status:** Active Research
**Version:** 1.0

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [The Three-Tier Model](#the-three-tier-model)
3. [Industry Patterns & Precedents](#industry-patterns--precedents)
4. [Architecture Options](#architecture-options)
5. [Integration Patterns](#integration-patterns)
6. [Data Flow Models](#data-flow-models)
7. [Memory & State Management](#memory--state-management)
8. [Protocol Options](#protocol-options)
9. [Security & Privacy Considerations](#security--privacy-considerations)
10. [Monetization & Sustainability](#monetization--sustainability)
11. [Open Questions](#open-questions)
12. [Research Tasks](#research-tasks)
13. [References & Resources](#references--resources)

---

## Executive Summary

### The Core Thesis

Build a **personal AI creative studio** where:
- **Luna** is the intelligent interface (like Photoshop)
- **Eclissi** is the orchestration hub (like Creative Cloud)
- **Eden** provides generative capabilities (like Firefly/Stock)

### Key Differentiators from Existing Solutions

| Aspect | Current AI Tools | Luna/Eclissi/Eden |
|--------|-----------------|-------------------|
| Memory | Stateless or session-only | Persistent, semantic, personal |
| Identity | Generic assistant | Sovereign companion with personality |
| Compute | Cloud-only | Hybrid local+cloud with tiered routing |
| Creative | Single-service | Multi-service orchestration |
| Ownership | Platform owns data | User owns data locally |

---

## The Three-Tier Model

### Tier 1: Luna (Client Layer)

**Role:** Personal AI companion and creative interface

**Characteristics:**
- Runs on user's device
- Maintains persistent memory and personality
- Handles voice, text, and multimodal input
- Provides the "face" of the system
- Makes routing decisions for simple queries

**Current Implementation:**
- Python-based engine
- Actor model (Director, Matrix, Scribe, Librarian)
- Revolving context window
- Consciousness state machine
- Local inference via MLX (optional)

**Key Questions:**
- [ ] Should Luna be a standalone app or embedded in other tools?
- [ ] How thin/thick should the client be?
- [ ] What must always stay local vs. can be delegated?

---

### Tier 2: Eclissi (Hub Layer)

**Role:** Orchestration, routing, and local intelligence

**Characteristics:**
- Always-running daemon
- Multi-model routing (T0-T4 tiers)
- Memory matrix (graph + vector store)
- Document ingestion (AI-BRARIAN)
- MCP server for tool access
- Desktop UI (Tauri)

**Current Implementation:**
- 5-tier model routing (MLX local + Claude API)
- SQLite + NetworkX graph store
- FTS5 full-text search
- 20+ MCP tools
- Tauri desktop shell

**Key Questions:**
- [ ] Should Eclissi be self-hosted only, or have a cloud option?
- [ ] How to handle multi-device sync?
- [ ] What's the right boundary between Eclissi and Luna Engine?

---

### Tier 3: Eden (Service Layer)

**Role:** Cloud generative capabilities and community

**Characteristics:**
- AI image/video generation
- Agent sessions and conversations
- Community creations and sharing
- Heavy compute offloading
- Specialized models (LoRAs, fine-tunes)

**Current Capabilities:**
- `/v2/tasks/create` - Image/video generation
- `/v2/sessions` - Agent chat sessions
- `/v2/agents` - Agent management
- `/v2/creations` - Community gallery
- Tool use within sessions

**Key Questions:**
- [ ] What's Eden's appetite for deeper integration?
- [ ] Can Luna become an "Eden Agent" with special capabilities?
- [ ] How to handle authentication across the ecosystem?

---

## Industry Patterns & Precedents

### Pattern 1: Adobe Creative Cloud Model

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Photoshop   │────▶│Creative Cloud│────▶│   Firefly    │
│  Illustrator │     │    (Hub)     │     │    Stock     │
│  Premiere    │     │              │     │    Fonts     │
└──────────────┘     └──────────────┘     └──────────────┘
     Local App          Sync/Auth          Cloud Services
```

**Strengths:**
- Clear separation of concerns
- Apps work offline with degraded features
- Unified identity across services
- Marketplace for extensions

**Weaknesses:**
- Subscription fatigue
- Heavy client installs
- Limited cross-app intelligence

**Applicability to Luna/Eclissi/Eden:**
- ✅ Clear tier separation
- ✅ Local-first with cloud enhancement
- ⚠️ Need to avoid subscription complexity
- ✅ Marketplace potential for agents/tools

---

### Pattern 2: Apple Intelligence Model

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Siri UI    │────▶│ Apple Neural │────▶│   OpenAI     │
│  App Intents │     │   Engine     │     │   (Fallback) │
└──────────────┘     └──────────────┘     └──────────────┘
   Interface         On-Device AI          Cloud Escalation
```

**Strengths:**
- Privacy-first (local processing)
- Seamless escalation to cloud
- Deep OS integration
- Consistent experience across devices

**Weaknesses:**
- Walled garden
- Limited third-party access
- Hardware requirements

**Applicability to Luna/Eclissi/Eden:**
- ✅ Tiered routing model (already in Eclissi)
- ✅ Privacy through local-first
- ⚠️ Need cross-platform strategy
- ✅ Escalation pattern matches T0→T4

---

### Pattern 3: Obsidian + Plugins Model

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Obsidian   │────▶│   Plugins    │────▶│   Sync/API   │
│   (Core)     │     │  (Community) │     │  (Optional)  │
└──────────────┘     └──────────────┘     └──────────────┘
   Local-First        Extensions          Cloud Services
```

**Strengths:**
- User owns all data (markdown files)
- Vibrant plugin ecosystem
- Works completely offline
- Optional cloud sync

**Weaknesses:**
- Plugin quality varies
- No built-in AI (yet)
- Sync is separate product

**Applicability to Luna/Eclissi/Eden:**
- ✅ Local-first data ownership
- ✅ Plugin/tool extensibility (MCP)
- ✅ Optional cloud enhancement
- 💡 Could Luna be an "Obsidian for AI"?

---

### Pattern 4: VSCode + Extensions + Copilot

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    VSCode    │────▶│  Extensions  │────▶│   Copilot    │
│   (Editor)   │     │ (Ecosystem)  │     │   (AI)       │
└──────────────┘     └──────────────┘     └──────────────┘
   Local App          Plugin System        Cloud AI
```

**Strengths:**
- Massive extension ecosystem
- AI deeply integrated into workflow
- Multiple AI providers possible
- Open source core

**Weaknesses:**
- Microsoft lock-in concerns
- Extension conflicts
- Performance with many extensions

**Applicability to Luna/Eclissi/Eden:**
- ✅ Extension model for tools
- ✅ Multiple AI backend support
- ✅ Deep workflow integration
- 💡 MCP as the "extension API"

---

### Pattern 5: Home Assistant Model

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  HA Core     │────▶│ Integrations │────▶│   Cloud      │
│  (Local)     │     │  (1000s)     │     │  (Optional)  │
└──────────────┘     └──────────────┘     └──────────────┘
   Self-Hosted        Device Adapters      Remote Access
```

**Strengths:**
- Completely self-hosted option
- Massive integration ecosystem
- Privacy-focused community
- Automations across services

**Weaknesses:**
- Setup complexity
- Maintenance burden
- Some features need cloud

**Applicability to Luna/Eclissi/Eden:**
- ✅ Self-hosted hub option
- ✅ Integration-first design
- ✅ Automation capabilities
- 💡 Could Eclissi be "Home Assistant for AI"?

---

## Architecture Options

### Option A: Monolithic Luna with Eden Plugin

```
┌─────────────────────────────────────────┐
│               LUNA                       │
│  ┌─────────────────────────────────┐    │
│  │         Core Engine             │    │
│  │  Memory │ Voice │ Personality   │    │
│  └─────────────────────────────────┘    │
│  ┌─────────────────────────────────┐    │
│  │         Tool Plugins            │    │
│  │  Eden │ Claude │ Files │ Web    │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

**Pros:**
- Simplest architecture
- Single codebase
- Easy deployment

**Cons:**
- No hub reusability
- Hard to scale
- Eclissi becomes redundant

**Verdict:** Good for MVP, doesn't leverage Eclissi investment

---

### Option B: Luna ↔ Eclissi ↔ Eden (Current Vision)

```
┌─────────┐     ┌─────────────┐     ┌─────────┐
│  LUNA   │◀───▶│   ECLISSI   │◀───▶│  EDEN   │
│ (Client)│     │    (Hub)    │     │(Service)│
└─────────┘     └─────────────┘     └─────────┘
```

**Pros:**
- Clear separation
- Hub is reusable
- Matches proven patterns
- Scales well

**Cons:**
- More complexity
- Inter-process communication
- More to maintain

**Verdict:** Best for long-term vision, matches industry patterns

---

### Option C: Luna as Eden Agent

```
┌─────────────────────────────────────────┐
│               EDEN                       │
│  ┌─────────────────────────────────┐    │
│  │      Agent: "Luna"              │    │
│  │  (Custom agent with memory)     │    │
│  └─────────────────────────────────┘    │
│  ┌─────────────────────────────────┐    │
│  │      Eclissi (Local Sync)       │    │
│  │  (Caches, offline, local LLM)   │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

**Pros:**
- Leverages Eden's infrastructure
- Luna as a "supercharged" Eden agent
- Community features built-in
- Simpler client

**Cons:**
- Depends on Eden's roadmap
- Less control over core
- Privacy concerns (data on Eden)

**Verdict:** Interesting hybrid, needs Eden partnership

---

### Option D: Federated Architecture

```
┌─────────┐     ┌─────────────┐     ┌─────────┐
│ Luna A  │────▶│             │◀────│ Luna B  │
│(Device 1)│    │   ECLISSI   │     │(Device 2)│
└─────────┘     │   NETWORK   │     └─────────┘
                │             │
┌─────────┐     │  ┌───────┐  │     ┌─────────┐
│  Eden   │◀───▶│  │ Sync  │  │◀───▶│ Claude  │
└─────────┘     │  └───────┘  │     └─────────┘
                └─────────────┘
```

**Pros:**
- Multi-device Luna
- Shared memory across devices
- Multiple service providers
- Resilient

**Cons:**
- Complex sync
- Conflict resolution
- Network dependency

**Verdict:** Future evolution, not MVP

---

## Integration Patterns

### Pattern 1: Direct Tool Integration

Luna registers Eden as tools via MCP:

```python
# Luna's tool registry
tools = [
    Tool(name="eden_create_image", ...),
    Tool(name="eden_create_video", ...),
    Tool(name="eden_chat_agent", ...),
]
```

**When to Use:**
- Simple, stateless operations
- User explicitly requests Eden features
- No ongoing session needed

**Example Flow:**
```
User: "Create an image of a cat"
Luna: [calls eden_create_image tool]
Eden: [returns image URL]
Luna: "Here's your cat image: [displays]"
```

---

### Pattern 2: Session Bridging

Luna maintains an ongoing Eden session:

```python
class EdenSessionBridge:
    def __init__(self, agent_id: str):
        self.session_id = None
        self.agent_id = agent_id

    async def ensure_session(self):
        if not self.session_id:
            self.session_id = await eden.create_session(self.agent_id)

    async def send(self, message: str, context: dict):
        await self.ensure_session()
        # Include Luna's context in the message
        enriched = f"[Context: {context}]\n\n{message}"
        return await eden.send_message(self.session_id, enriched)
```

**When to Use:**
- Ongoing creative collaboration
- Eden agent has specialized knowledge
- Multi-turn creative workflows

**Example Flow:**
```
User: "Let's design a game character"
Luna: [creates Eden session with character design agent]
Eden Agent: "What style? Fantasy, sci-fi, cartoon?"
User: "Fantasy warrior"
Eden Agent: [generates concept art]
Luna: [stores in memory: "User's game character project"]
```

---

### Pattern 3: Memory Sync

Luna's memories enhance Eden's context:

```python
async def eden_with_memory(prompt: str):
    # Get relevant memories from Luna
    memories = await luna.memory.query(prompt, limit=5)

    # Format as context
    context = "\n".join([
        f"- {m.content} ({m.timestamp})"
        for m in memories
    ])

    # Send to Eden with context
    return await eden.create(
        prompt=prompt,
        context=f"User context:\n{context}"
    )
```

**When to Use:**
- Personalized generations
- Referencing past creations
- Maintaining style consistency

**Example Flow:**
```
User: "Make another image like the one from Tuesday"
Luna: [queries memory for Tuesday's images]
Memory: "Generated cyberpunk cityscape with neon colors"
Luna: [sends to Eden with style context]
Eden: [generates similar style image]
```

---

### Pattern 4: Agent Delegation

Luna delegates to specialized Eden agents:

```python
class AgentRouter:
    agents = {
        "art": "eden_agent_id_for_art",
        "video": "eden_agent_id_for_video",
        "music": "eden_agent_id_for_music",
    }

    async def delegate(self, task_type: str, task: str):
        agent_id = self.agents.get(task_type)
        if agent_id:
            return await eden.chat_with_agent(agent_id, task)
        return None
```

**When to Use:**
- Task requires specialized knowledge
- Luna lacks capability
- Parallelizing creative work

**Example Flow:**
```
User: "Create a music video with AI"
Luna: [decomposes task]
  → Art Agent: "Generate storyboard frames"
  → Video Agent: "Animate the frames"
  → Music Agent: "Compose backing track"
Luna: [orchestrates and combines results]
```

---

### Pattern 5: Bidirectional Agents

Luna IS an Eden agent that can call back:

```
┌─────────────────────────────────────────┐
│                 EDEN                     │
│                                          │
│  ┌──────────┐         ┌──────────┐      │
│  │  User    │ ──────▶ │  Luna    │      │
│  │          │         │  Agent   │      │
│  └──────────┘         └────┬─────┘      │
│                            │            │
│                            ▼            │
│                    ┌──────────────┐     │
│                    │ Luna Local   │     │
│                    │ (via webhook)│     │
│                    └──────────────┘     │
└─────────────────────────────────────────┘
```

**When to Use:**
- Luna needs to be accessible from anywhere
- Shared Luna with multiple users
- Luna as a service

**Considerations:**
- Requires public endpoint or tunnel
- Privacy implications
- Latency for local operations

---

## Data Flow Models

### Model A: Luna-Centric (Local First)

```
┌─────────────────────────────────────────────────────────┐
│                    LUNA (Local)                          │
│                                                          │
│  User Input ──▶ Classifier ──▶ Router ──▶ Response      │
│                     │            │                       │
│                     ▼            ▼                       │
│              ┌──────────┐  ┌──────────┐                 │
│              │ Local LLM│  │ Memory   │                 │
│              └──────────┘  └──────────┘                 │
│                     │            │                       │
│                     ▼            ▼                       │
│              ┌──────────────────────┐                   │
│              │   External Services   │                   │
│              │   (Eden, Claude, etc) │                   │
│              └──────────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

**Data Stays Local:**
- All conversation history
- User preferences
- Memory graph
- Personality state

**Goes to Cloud:**
- Generation requests (stripped of unnecessary context)
- Tool calls
- Model inference (when escalated)

---

### Model B: Hub-Centric (Eclissi Orchestrates)

```
                         ┌─────────────┐
                         │   ECLISSI   │
                         │    (Hub)    │
                         └──────┬──────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
        ▼                       ▼                       ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│    LUNA      │       │   MEMORY     │       │   SERVICES   │
│  (Interface) │       │   MATRIX     │       │(Eden, Claude)│
└──────────────┘       └──────────────┘       └──────────────┘
```

**Eclissi Controls:**
- Routing decisions
- Memory operations
- Service orchestration
- Model lifecycle

**Luna Becomes:**
- Pure interface layer
- Voice/text I/O
- Display/feedback

---

### Model C: Event-Driven (Pub/Sub)

```
┌─────────────────────────────────────────────────────────┐
│                    EVENT BUS                             │
└───┬─────────────┬─────────────┬─────────────┬───────────┘
    │             │             │             │
    ▼             ▼             ▼             ▼
┌───────┐    ┌───────┐    ┌───────┐    ┌───────┐
│ Luna  │    │Memory │    │ Eden  │    │Claude │
│       │    │Matrix │    │       │    │       │
└───────┘    └───────┘    └───────┘    └───────┘

Events:
- user.input
- memory.query
- generation.request
- generation.complete
- agent.message
```

**Pros:**
- Loose coupling
- Easy to add services
- Async by nature

**Cons:**
- Debugging complexity
- Event ordering
- Eventual consistency

---

## Memory & State Management

### What Needs to Persist

| Data Type | Location | Sync Strategy |
|-----------|----------|---------------|
| Conversation history | Local (Luna/Eclissi) | Summarize old turns |
| User preferences | Local | Explicit backup |
| Memory graph | Local (Eclissi) | None (local only) |
| Eden creations | Eden cloud | Reference IDs stored locally |
| Session state | Local | Per-session, ephemeral |
| Personality evolution | Local | Periodic snapshots |

### Memory Architecture Options

**Option 1: Single Source of Truth (Eclissi)**
```
All memory lives in Eclissi's Memory Matrix
Luna queries via MCP tools
Eden gets context via API enrichment
```

**Option 2: Distributed Memory**
```
Luna: Conversation buffer (recent)
Eclissi: Long-term memory graph
Eden: Creation history and agent memories
Sync: Periodic reconciliation
```

**Option 3: Layered Memory**
```
L1 (Hot): Luna's current context window
L2 (Warm): Eclissi's recent memories
L3 (Cold): Archived conversations
L4 (External): Eden creations, external data
```

### Cross-System References

How to reference an Eden creation in Luna's memory:

```python
@dataclass
class MemoryNode:
    id: str
    content: str
    type: str  # "fact", "creation", "conversation"
    source: str  # "luna", "eden", "user"
    external_ref: Optional[dict] = None  # {"service": "eden", "id": "creation_123"}
```

---

## Protocol Options

### Option 1: MCP (Model Context Protocol)

**Current Status:** Already implemented in Eclissi

**Pros:**
- Standard protocol
- Claude-native
- Tool-based interaction
- Growing ecosystem

**Cons:**
- Anthropic-controlled
- Limited streaming
- Request/response only

**Use For:**
- Luna ↔ Eclissi tools
- Claude ↔ Luna tools
- Standardized interfaces

---

### Option 2: REST/HTTP

**Current Status:** Eden uses REST

**Pros:**
- Universal
- Well-understood
- Easy to debug
- Cacheable

**Cons:**
- Polling for long tasks
- No push notifications
- Overhead for small messages

**Use For:**
- Eden API integration
- Web interfaces
- External services

---

### Option 3: WebSockets

**Pros:**
- Real-time bidirectional
- Streaming support
- Low latency

**Cons:**
- Connection management
- Reconnection logic
- Stateful

**Use For:**
- Real-time voice
- Generation progress
- Live collaboration

---

### Option 4: gRPC

**Pros:**
- Fast binary protocol
- Strong typing
- Streaming built-in
- Multi-language

**Cons:**
- More complex setup
- Harder to debug
- Overkill for simple cases

**Use For:**
- High-performance internal communication
- Multi-language services
- Microservices

---

### Option 5: Local IPC (Unix Sockets / Named Pipes)

**Pros:**
- Fastest local communication
- No network overhead
- Secure (local only)

**Cons:**
- Local only
- Platform-specific
- No remote access

**Use For:**
- Luna ↔ Eclissi (same machine)
- High-frequency internal calls
- Sensitive data

---

## Security & Privacy Considerations

### Data Classification

| Data Type | Sensitivity | Can Leave Device? |
|-----------|-------------|-------------------|
| API keys | Critical | Never |
| Personal memories | High | User consent only |
| Preferences | Medium | Anonymized OK |
| Generation prompts | Medium | With purpose limitation |
| Created content | Low | User controls |
| Conversation logs | High | User consent only |

### Privacy-Preserving Patterns

**Pattern 1: Local-First Processing**
```
All reasoning happens locally
Only generation requests go to cloud
Minimal context sent
Results cached locally
```

**Pattern 2: Differential Privacy**
```
Add noise to queries
Aggregate before sending
No individual identification
```

**Pattern 3: End-to-End Encryption**
```
Encrypt before leaving device
Only user can decrypt
Services see encrypted blobs
```

### Authentication Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    ECLISSI                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │              Credential Vault                    │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐        │    │
│  │  │ Eden Key │ │Claude Key│ │ User ID  │        │    │
│  │  └──────────┘ └──────────┘ └──────────┘        │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
              Service calls include auth
              Luna never sees raw keys
```

---

## Monetization & Sustainability

### Cost Structure

| Component | Cost Type | Who Pays? |
|-----------|-----------|-----------|
| Luna Engine | Open source / Free | User (time) |
| Eclissi Hub | Self-hosted | User (compute) |
| Eden API | Usage-based | User (credits) |
| Claude API | Usage-based | User (API key) |
| Local LLMs | One-time download | User (storage) |

### Potential Models

**Model 1: Fully Self-Hosted**
```
User runs everything locally
Pays only for external APIs
Maximum privacy, maximum control
```

**Model 2: Freemium Hub**
```
Basic Eclissi: Free, self-hosted
Premium Eclissi: Cloud sync, backups, team features
Eden/Claude: Usage-based
```

**Model 3: Eden Partnership**
```
Luna as premium Eden feature
Eden handles billing
Revenue share
```

**Model 4: Marketplace**
```
Free core
Paid agents/tools/personalities
Creator economy for AI
```

---

## Open Questions

### Technical

- [ ] What's the right protocol between Luna and Eclissi?
- [ ] How to handle offline mode gracefully?
- [ ] What's the minimum viable local LLM for good UX?
- [ ] How to sync memory across devices?
- [ ] What's the latency budget for each tier?

### Product

- [ ] Who is the primary user? Creator? Developer? Consumer?
- [ ] What's the killer use case that requires all three tiers?
- [ ] How to onboard users to a complex system?
- [ ] What's the competitive moat?

### Business

- [ ] What's Eden's appetite for partnership?
- [ ] Is there a path to sustainability without subscription?
- [ ] How to balance open source with business needs?
- [ ] What's the go-to-market strategy?

### Philosophy

- [ ] What does "sovereign AI" mean in practice?
- [ ] How to ensure Luna remains user-aligned?
- [ ] What are the ethical implications of persistent AI memory?
- [ ] How to handle AI personality evolution responsibly?

---

## Research Tasks

### Phase 1: Foundation (Current)

- [x] Document current Luna architecture
- [x] Document current Eclissi architecture
- [x] Analyze Eden API capabilities
- [x] Map industry patterns
- [ ] Build Eden adapter prototype
- [ ] Test basic integration flow

### Phase 2: Integration

- [ ] Implement Eden tools in Luna
- [ ] Test session bridging
- [ ] Measure latency and UX
- [ ] Prototype memory sync
- [ ] User testing (self)

### Phase 3: Refinement

- [ ] Optimize routing decisions
- [ ] Improve context handoff
- [ ] Build better UI for creative workflow
- [ ] Document patterns that work
- [ ] Identify patterns that don't

### Phase 4: Expansion

- [ ] Multi-device exploration
- [ ] Additional service integrations
- [ ] Community features
- [ ] Marketplace exploration

---

## References & Resources

### Eden

- **Repository:** https://github.com/edenartlab/hello-eden
- **API Base:** https://api.eden.art
- **Documentation:** [To be found]
- **SDK:** `@edenlabs/eden-sdk`

### Luna

- **Location:** `_HeyLuna_BETA/_LunaEngine_BetaProject_V2.0_Root/`
- **Key Files:**
  - `src/luna/engine.py` - Core engine
  - `src/luna/tools/registry.py` - Tool system
  - `src/luna/substrate/memory.py` - Memory system

### Eclissi

- **Location:** `_HeyLuna_BETA/_Eclessi_BetaProject_Root/`
- **Key Files:**
  - `src/eclissi/orchestrator.py` - Main brain
  - `src/memory_matrix/` - Graph store
  - `src/aibrarian/` - Document ingestion
  - `src/mcp/` - MCP server

### Industry References

- Adobe Creative Cloud Architecture
- Apple Intelligence Technical Overview
- Obsidian Plugin System
- VSCode Extension API
- Home Assistant Architecture
- MCP Specification

### Academic/Theoretical

- "Attention Is All You Need" (Transformer architecture)
- "Constitutional AI" (Anthropic)
- "Retrieval-Augmented Generation" (RAG patterns)
- "Memory Networks" (Facebook AI)

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **Luna** | Personal AI companion and interface layer |
| **Eclissi** | Orchestration hub and local intelligence daemon |
| **Eden** | Cloud generative AI service |
| **Memory Matrix** | Graph + vector store for semantic memory |
| **AI-BRARIAN** | Document ingestion and management system |
| **MCP** | Model Context Protocol - Anthropic's tool standard |
| **Tier (T0-T4)** | Model routing levels from classifier to full Claude |
| **Actor** | Concurrent processing unit in Luna's architecture |

---

## Appendix B: Decision Log

| Date | Decision | Rationale | Status |
|------|----------|-----------|--------|
| 2025-02-05 | Adopt three-tier model | Matches industry patterns, clear separation | Active |
| TBD | Protocol choice | Pending analysis | Open |
| TBD | Memory sync strategy | Pending requirements | Open |

---

## Appendix C: Experiment Log

Track experiments and their outcomes here.

| Experiment | Hypothesis | Result | Learning |
|------------|------------|--------|----------|
| Eden adapter prototype | Can integrate Eden as Luna tool | Pending | - |

---

*Document maintained by the Luna Engine Team*
*Last updated: 2025-02-05*
