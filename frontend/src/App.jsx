import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import CameraPreview from "./CameraPreview";
import CompanionDesigner from "./CompanionDesigner";
import MemorySettings from "./MemorySettings";
import RobotCharacter from "./RobotCharacter";
import { defaultLooks } from "./robotLooks";
import { apiJson, apiRequest, getSessionId, localStorageJson } from "./api";

const USER_KEY = "mirrorling:user-id";
const ACTIVE_COMPANION_PREFIX = "mirrorling:active-companion:";
const BUILTIN_ORDER = ["moss", "nova", "echo", "zen"];
const COMPANION_COPY = {
  moss: { tagline: "A little warmth, a little wonder.", signal: "Easygoing · Curious · Playful" },
  nova: { tagline: "A softer place to land.", signal: "Thoughtful · Patient · Present" },
  echo: { tagline: "Small steps. Real momentum.", signal: "Focused · Bold · Encouraging" },
  zen: { tagline: "Find the quiet current.", signal: "Grounded · Clear · Calm" },
};

let bootstrapPromise;

async function initializeIdentity() {
  if (!bootstrapPromise) {
    bootstrapPromise = (async () => {
      let user = null;
      const savedId = localStorage.getItem(USER_KEY);
      if (savedId) {
        try {
          user = await apiJson(`/users/${encodeURIComponent(savedId)}`);
        } catch (error) {
          if (error.status !== 404 && error.status !== 422) throw error;
          localStorage.removeItem(USER_KEY);
        }
      }
      if (!user) {
        user = await apiJson("/users", "POST");
        localStorage.setItem(USER_KEY, user.user_id);
      }

      let companions = await apiJson(`/users/${user.user_id}/companions`);
      const relationships = await apiJson(`/users/${user.user_id}/relationships`);
      const relationshipMap = new Map(relationships.map((item) => [item.companion_id, item]));

      for (const companion of companions) {
        if (relationshipMap.has(companion.companion_id)) continue;
        try {
          const relationship = await apiJson(
            `/users/${user.user_id}/relationships`,
            "POST",
            { companion_id: companion.companion_id },
          );
          relationshipMap.set(companion.companion_id, relationship);
        } catch (error) {
          if (error.status !== 409) throw error;
          const latest = await apiJson(`/users/${user.user_id}/relationships`);
          for (const relationship of latest) relationshipMap.set(relationship.companion_id, relationship);
        }
      }

      companions = companions
        .map((item) => ({ ...item, relationship: relationshipMap.get(item.companion_id) }))
        .sort((left, right) => {
          const leftIndex = BUILTIN_ORDER.indexOf(left.companion_id);
          const rightIndex = BUILTIN_ORDER.indexOf(right.companion_id);
          if (leftIndex !== -1 || rightIndex !== -1) {
            return (leftIndex === -1 ? 99 : leftIndex) - (rightIndex === -1 ? 99 : rightIndex);
          }
          return left.name.localeCompare(right.name);
        });
      return { user, companions };
    })().catch((error) => {
      bootstrapPromise = null;
      throw error;
    });
  }
  return bootstrapPromise;
}

function displayName(companion) {
  return companion?.name || "Moss";
}

function getLocalSettings(userId, companion) {
  if (!companion) return {};
  return localStorageJson(
    `mirrorling:companion-settings:${userId}:${companion.companion_id}`,
    {},
  );
}

function getLocalVoicePreference(userId, companionId) {
  return localStorageJson(
    `mirrorling:voice-settings:${userId}:${companionId}`,
    {},
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [companions, setCompanions] = useState([]);
  const [activeCompanionId, setActiveCompanionId] = useState(null);
  const [screen, setScreen] = useState("dashboard");
  const [designerCompanion, setDesignerCompanion] = useState(null);
  const [initializing, setInitializing] = useState(true);
  const [bootError, setBootError] = useState("");
  const [health, setHealth] = useState("checking");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [conversationMap, setConversationMap] = useState({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState("Ready when you are");
  const [latestVoice, setLatestVoice] = useState(null);
  const [latestFace, setLatestFace] = useState(null);
  const [playing, setPlaying] = useState(false);

  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const faceObservationsRef = useRef([]);
  const recordingIntervalRef = useRef(null);
  const playbackRef = useRef(null);
  const playbackUrlRef = useRef(null);
  const conversationEndRef = useRef(null);

  const activeCompanion = companions.find((item) => item.companion_id === activeCompanionId) || null;
  const activeRelationship = activeCompanion?.relationship || null;
  const conversationKey = user && activeCompanion
    ? `${user.user_id}:${activeCompanion.companion_id}`
    : "";
  const messages = conversationMap[conversationKey] || [];
  const localSettings = getLocalSettings(user?.user_id, activeCompanion);
  const activeLooks = localSettings.looks || defaultLooks(activeCompanion?.companion_id || "moss");
  const companionCopy = COMPANION_COPY[activeCompanion?.companion_id] || {
    tagline: localSettings.tagline || "A companion shaped around your way of seeing the world.",
    signal: "Your companion · Here with you",
  };

  useEffect(() => {
    let active = true;
    async function boot() {
      setInitializing(true);
      setBootError("");
      try {
        const [identity, healthResult] = await Promise.all([
          initializeIdentity(),
          apiJson("/health").catch(() => ({ mongodb: "unavailable" })),
        ]);
        if (!active) return;
        setUser(identity.user);
        setCompanions(identity.companions);
        setHealth(healthResult.mongodb || "unavailable");
        const storedSelection = localStorage.getItem(`${ACTIVE_COMPANION_PREFIX}${identity.user.user_id}`);
        const selected = identity.companions.find((item) => item.companion_id === storedSelection)
          || identity.companions.find((item) => item.companion_id === "moss")
          || identity.companions[0];
        if (selected) {
          setActiveCompanionId(selected.companion_id);
          localStorage.setItem(`${ACTIVE_COMPANION_PREFIX}${identity.user.user_id}`, selected.companion_id);
        }
      } catch (error) {
        if (active) setBootError(error.message || "Mirrorling could not initialise.");
      } finally {
        if (active) setInitializing(false);
      }
    }
    boot();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, busy]);

  useEffect(() => () => {
    playbackRef.current?.pause();
    if (playbackUrlRef.current) URL.revokeObjectURL(playbackUrlRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  const handleEmotionResult = useCallback((observation) => {
    setLatestFace(observation || null);
    const recordingInterval = recordingIntervalRef.current;

    if (
      observation &&
      recordingInterval &&
      observation.capturedAt >= recordingInterval.start &&
      (recordingInterval.end === null ||
        observation.capturedAt <= recordingInterval.end)
    ) {
      faceObservationsRef.current.push(observation);
    }
  }, []);

  const refreshRelationships = useCallback(async () => {
    if (!user) return;
    try {
      const relationships = await apiJson(`/users/${user.user_id}/relationships`);
      setCompanions((current) => current.map((companion) => ({
        ...companion,
        relationship: relationships.find((item) => item.companion_id === companion.companion_id)
          || companion.relationship,
      })));
    } catch (error) {
      setStatus(error.message);
    }
  }, [user]);

  async function speakText(text, companion = activeCompanion, targetUser = user) {
    try {
      playbackRef.current?.pause();
      if (playbackUrlRef.current) URL.revokeObjectURL(playbackUrlRef.current);
      const voicePreference = companion && targetUser
        ? getLocalVoicePreference(targetUser.user_id, companion.companion_id)
        : {};
      setStatus(`${displayName(companion)} is preparing a voice response…`);
      const audioBlob = await apiRequest("/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          ...(companion ? { companion_id: companion.companion_id } : {}),
          ...(voicePreference && Object.hasOwn(voicePreference, "voice_id")
            ? { voice_id: voicePreference.voice_id }
            : {}),
        }),
      });
      const audioUrl = URL.createObjectURL(audioBlob);
      playbackUrlRef.current = audioUrl;
      const player = new Audio(audioUrl);
      playbackRef.current = player;
      player.onended = () => {
        URL.revokeObjectURL(audioUrl);
        playbackUrlRef.current = null;
        playbackRef.current = null;
        setPlaying(false);
        setStatus("Ready when you are");
      };
      player.onerror = () => {
        URL.revokeObjectURL(audioUrl);
        playbackUrlRef.current = null;
        playbackRef.current = null;
        setPlaying(false);
        setStatus("Audio playback failed.");
      };
      await player.play();
      setPlaying(true);
      setStatus(`${displayName(companion)} is speaking${audioBlob.type === "audio/wav" ? " with local Piper" : ""}`);
    } catch (error) {
      setPlaying(false);
      setStatus(`Voice playback failed: ${error.message}`);
  }
  }

  function stopSpeech() {
    playbackRef.current?.pause();
    playbackRef.current = null;
    if (playbackUrlRef.current) URL.revokeObjectURL(playbackUrlRef.current);
    playbackUrlRef.current = null;
    setPlaying(false);
    setStatus("Ready when you are");
  }

  async function sendMessage(text, perception) {
    if (!text || !user || !activeCompanion || busy) return;
    const targetUser = user;
    const targetCompanion = activeCompanion;
    const key = `${targetUser.user_id}:${targetCompanion.companion_id}`;
    const sessionId = getSessionId(targetUser.user_id, targetCompanion.companion_id);
    const userMessage = { id: crypto.randomUUID(), role: "user", text, time: new Date() };
    setConversationMap((current) => ({ ...current, [key]: [...(current[key] || []), userMessage] }));
    setBusy(true);
    setStatus(`${targetCompanion.name} is thinking…`);
    try {
      const result = await apiJson("/chat", "POST", {
        message: text,
        session_id: sessionId,
        user_id: targetUser.user_id,
        companion_id: targetCompanion.companion_id,
        ...(perception ? { perception } : {}),
      });
      setConversationMap((current) => ({
        ...current,
        [key]: [...(current[key] || []), {
          id: crypto.randomUUID(),
          role: "assistant",
          text: result.reply,
          time: new Date(),
        }],
      }));
      setStatus("Ready when you are");
      await refreshRelationships();
      await speakText(result.reply, targetCompanion, targetUser);
    } catch (error) {
      setConversationMap((current) => ({
        ...current,
        [key]: [...(current[key] || []), {
          id: crypto.randomUUID(),
          role: "error",
          text: error.message || "The message could not be sent.",
          time: new Date(),
        }],
      }));
      setStatus(error.message || "The message could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  async function processRecording(blob, recordingInterval) {
    if (!blob.size) {
      setStatus("No audio recorded. Try again.");
      recordingIntervalRef.current = null;
      return;
    }

    setTranscribing(true);
    setStatus("Understanding your voice…");

    try {
      const formData = new FormData();
      formData.append("file", blob, "recording.webm");

      const data = await apiRequest("/transcribe", {
        method: "POST",
        body: formData,
      });
      const text = data.transcription.trim();

      if (!text) throw new Error("I couldn't hear anything clearly.");

      const perception = {};

      if (data.voice_analysis) {
        perception.voice = {
          captured_at: recordingInterval.end,
          measurements: data.voice_analysis
        };
        setLatestVoice(data.voice_analysis);
      }

      const faceObservation = faceObservationsRef.current
        .filter(
          (observation) =>
            observation.capturedAt >= recordingInterval.start &&
            observation.capturedAt <= recordingInterval.end
        )
        .reduce(
          (latest, observation) =>
            !latest || observation.capturedAt > latest.capturedAt
              ? observation
              : latest,
          null
        );

      if (faceObservation) {
        perception.face = {
          captured_at: faceObservation.capturedAt,
          measurements: faceObservation.measurements
        };
      }

      setStatus("Transcript ready · sending to companion");
      await sendMessage(text, Object.keys(perception).length > 0 ? perception : undefined);
    } catch (error) {
      setStatus(error.message);
    } finally {
      setTranscribing(false);
      recordingIntervalRef.current = null;
    }
  }

  async function handleMicClick() {
    if (recording) {
      if (recordingIntervalRef.current) {
        recordingIntervalRef.current.end = Date.now();
      }
      recorderRef.current?.stop();
      setRecording(false);
      setStatus("Processing your recording...");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      streamRef.current = stream;
      chunksRef.current = [];

      const recorder = new MediaRecorder(stream);
      const recordingInterval = { start: Date.now(), end: null };

      recorderRef.current = recorder;
      recordingIntervalRef.current = recordingInterval;
      faceObservationsRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        recordingInterval.end ??= Date.now();
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });

        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;

        processRecording(blob, recordingInterval);
      };

      recorder.start();
      setRecording(true);
      setStatus("Listening to you...");
    } catch (error) {
      setStatus(
        error.name === "NotAllowedError"
          ? "Please allow microphone access."
          : "Could not access your microphone."
      );
    }
  }

  async function handleTextSubmit(event) {
    event.preventDefault();

    const text = message.trim();
    if (!text || busy || transcribing) return;

    setMessage("");
    await sendMessage(text);
  }

  async function selectCompanion(companionId) {
    if (!user || busy || recording || transcribing) return;
    stopSpeech();
    setActiveCompanionId(companionId);
    localStorage.setItem(`${ACTIVE_COMPANION_PREFIX}${user.user_id}`, companionId);
    setLatestVoice(null);
    setLatestFace(null);
  }

  function openDesigner(companion = null) {
    setDesignerCompanion(companion || null);
    setScreen("designer");
  }

  async function handleCompanionSaved(createdCompanion) {
    let relationship;
    try {
      relationship = await apiJson(`/users/${user.user_id}/relationships`, "POST", {
        companion_id: createdCompanion.companion_id,
      });
    } catch (error) {
      if (error.status !== 409) throw error;
      const relationships = await apiJson(`/users/${user.user_id}/relationships`);
      relationship = relationships.find((item) => item.companion_id === createdCompanion.companion_id);
    }
    setCompanions((current) => [...current.filter((item) => item.companion_id !== createdCompanion.companion_id), {
      ...createdCompanion,
      relationship,
    }]);
    setActiveCompanionId(createdCompanion.companion_id);
    localStorage.setItem(`${ACTIVE_COMPANION_PREFIX}${user.user_id}`, createdCompanion.companion_id);
    setScreen("dashboard");
  }

  async function completeFirstMeeting() {
    if (!user || !activeCompanion || !activeRelationship) return;
    setStatus("Updating this relationship…");
    try {
      if (activeRelationship.first_meeting_status === "not_started") {
        await apiJson(`/users/${user.user_id}/relationships/${activeCompanion.companion_id}/first-meeting`, "PATCH", { status: "in_progress" });
      }
      const updated = await apiJson(`/users/${user.user_id}/relationships/${activeCompanion.companion_id}/first-meeting`, "PATCH", { status: "completed" });
      setCompanions((current) => current.map((item) => item.companion_id === activeCompanion.companion_id ? { ...item, relationship: updated } : item));
      setStatus("First Meeting complete");
    } catch (error) {
      setStatus(error.message);
    }
  }

  function updateMemorySettings(updatedUser, updatedRelationship) {
    setUser(updatedUser);
    if (updatedRelationship && activeCompanion) {
      setCompanions((current) => current.map((item) => item.companion_id === activeCompanion.companion_id ? { ...item, relationship: updatedRelationship } : item));
    }
  }

  const mood = recording ? "listening" : busy ? "thinking" : playing ? "speaking" : "idle";

  if (initializing) {
    return <main className="boot-screen"><div className="boot-mark">M</div><p>Connecting to your companions…</p></main>;
  }

  if (bootError) {
    return (
      <main className="boot-screen">
        <div className="boot-mark">M</div>
        <h1>Mirrorling is waiting for its backend.</h1>
        <p>{bootError}</p>
        <button className="primary-button" onClick={() => window.location.reload()}>Try again</button>
      </main>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand-lockup" href="#dashboard" onClick={(event) => { event.preventDefault(); setScreen("dashboard"); }}>
          <span className="brand-mark">M</span><span><strong>MIRRORLING</strong><small>COMPANION SYSTEMS</small></span>
        </a>
        <nav className="top-nav" aria-label="Main navigation">
          <button className={screen === "dashboard" ? "active" : ""} onClick={() => setScreen("dashboard")}>Companion</button>
          <button className={screen === "designer" ? "active" : ""} onClick={() => openDesigner(activeCompanion)}>Studio</button>
        </nav>
        <div className="top-actions">
          <span className={`connection-state ${health}`}><i />{health === "connected" ? "LOCAL SYSTEM ONLINE" : "BACKEND UNAVAILABLE"}</span>
          <button className="memory-open" onClick={() => setSettingsOpen(true)} disabled={!user}>Memory <span className="memory-glyph">◈</span></button>
        </div>
      </header>

      {screen === "designer" ? (
        <CompanionDesigner
          key={designerCompanion?.companion_id || "new-companion"}
          userId={user?.user_id}
          companion={designerCompanion}
          onSaved={handleCompanionSaved}
          onBack={() => setScreen("dashboard")}
          onOpenMemory={() => setSettingsOpen(true)}
        />
      ) : (
        <main className="dashboard-layout">
          <aside className="companion-sidebar">
            <section className={`presence-stage robot-tone-${activeCompanion?.companion_id || "moss"}`}>
              <div className="presence-topline"><span className="eyebrow">YOUR COMPANION</span><span className="online-indicator"><i /> HERE</span></div>
              <div className="presence-art"><RobotCharacter companion={activeCompanion} looks={activeLooks} mood={mood} /></div>
              <div className="presence-caption">
                <div><h1>{displayName(activeCompanion)}</h1><p>{localSettings.tagline || companionCopy.tagline}</p></div>
                {activeCompanion?.kind === "custom" && <button className="small-link" onClick={() => openDesigner(activeCompanion)}>Edit design</button>}
              </div>
              <span className="personality-signal">{companionCopy.signal}</span>
            </section>

            <section className="companion-selector">
              <div className="section-heading"><div><p className="eyebrow">YOUR COMPANIONS</p><span>Choose who’s here</span></div><button className="add-companion" onClick={() => openDesigner(null)} aria-label="Add companion">＋</button></div>
              <div className="companion-list">
                {companions.map((item) => {
                  const itemSettings = getLocalSettings(user.user_id, item);
                  return (
                    <button
                      key={item.companion_id}
                      className={`companion-choice ${item.companion_id === activeCompanionId ? "selected" : ""}`}
                      onClick={() => selectCompanion(item.companion_id)}
                      disabled={busy || transcribing || recording}
                    >
                      <span className={`mini-robot mini-${item.companion_id}`}><RobotCharacter companion={item} looks={itemSettings.looks || defaultLooks(item.companion_id)} compact /></span>
                      <span className="choice-copy"><strong>{item.name}</strong><small>{COMPANION_COPY[item.companion_id]?.signal || itemSettings.tagline || "Custom companion"}</small></span>
                      {item.companion_id === activeCompanionId && <i className="choice-active" />}
                    </button>
                  );
                })}
                <button className="add-companion-row" onClick={() => openDesigner(null)}><span>＋</span> Design a companion</button>
              </div>
            </section>
          </aside>

          <section className="conversation-panel">
            <header className="conversation-header">
                <div><p className="eyebrow">PRIVATE CHANNEL / {activeCompanion?.name?.toUpperCase() || "MOSS"}</p><h2>Conversation</h2></div>
                <span className="chat-status" aria-live="polite">{status}</span>
              <div className="conversation-meta"><span className="session-pip" /> SESSION ACTIVE</div>
            </header>
            {activeRelationship && activeRelationship.first_meeting_status !== "completed" && (
              <section className="first-meeting-banner">
                <div className="first-meeting-symbol">✳</div>
                <div><strong>{activeRelationship.first_meeting_status === "not_started" ? `A first hello with ${activeCompanion.name}` : `Getting to know ${activeCompanion.name}`}</strong><p>{activeRelationship.first_meeting_status === "not_started" ? `${activeCompanion.name} will introduce themself when you begin chatting.` : "This companion’s first meeting is in progress."}</p></div>
                {activeRelationship.first_meeting_status === "in_progress" && <button onClick={completeFirstMeeting}>Mark complete</button>}
              </section>
            )}
            <div className="message-list" aria-live="polite">
              {messages.length === 0 && (
                <div className="conversation-empty">
                  <span className="empty-orbit">◌</span>
                  <p>{activeRelationship?.first_meeting_status === "not_started" ? `Say hello to ${activeCompanion?.name || "your companion"}.` : `The channel is quiet. Start wherever you like.`}</p>
                  <small>Your conversation stays separate for each companion.</small>
                </div>
              )}
              {messages.map((item) => (
                <article key={item.id} className={`chat-message ${item.role}`}>
                  {item.role === "assistant" && <span className={`message-avatar avatar-${activeCompanion?.companion_id || "moss"}`}>{activeCompanion?.name?.slice(0, 1) || "M"}</span>}
                  <div className="message-body">
                    <div className="message-meta"><strong>{item.role === "user" ? "You" : item.role === "error" ? "Connection" : activeCompanion?.name || "Moss"}</strong><time>{new Date(item.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>
                    <p>{item.text}</p>
                  </div>
                </article>
              ))}
              {busy && <div className="typing-line"><span className="typing-dots"><i /><i /><i /></span><span>{activeCompanion?.name || "Moss"} is thinking</span></div>}
              <div ref={conversationEndRef} />
            </div>
            <form className="composer" onSubmit={handleTextSubmit}>
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); handleTextSubmit(event); } }}
                placeholder={`Message ${activeCompanion?.name || "Moss"}…`}
                disabled={busy || transcribing || recording || !activeCompanion}
                rows={1}
              />
              <div className="composer-actions">
                <div className="composer-left">
                  <button type="button" className={`round-action ${recording ? "recording" : ""}`} onClick={handleMicClick} disabled={busy || !activeCompanion} aria-label={recording ? "Stop recording" : "Start microphone recording"} title={recording ? "Stop recording" : "Speak"}>{recording ? "■" : "⌁"}</button>
                  {playing && <button type="button" className="round-action stop-audio" onClick={stopSpeech} aria-label="Stop voice playback" title="Stop playback">Ⅱ</button>}
                  <span className="composer-hint">{recording ? "Listening · tap to finish" : "Enter to send · Shift + Enter for a new line"}</span>
                </div>
                <button className="send-button" type="submit" disabled={!message.trim() || busy || transcribing || recording || !activeCompanion}>Send <span>↗</span></button>
              </div>
            </form>
          </section>

          <aside className="insight-column">
            <section className="camera-section">
              <div className="side-heading"><div><p className="eyebrow">PRESENCE INPUT</p><h2>Camera</h2></div><span className="privacy-tag">USER CONTROLLED</span></div>
              <CameraPreview onEmotionResult={handleEmotionResult} />
            </section>
            <section className="perception-panel">
              <div className="side-heading"><div><p className="eyebrow">LIVE SIGNALS</p><h2>Perception</h2></div><span className="uncertain-tag">ESTIMATES</span></div>
              <p className="perception-note">Model cues are uncertain and never define how you feel.</p>
              <div className="signal-list">
                <div className={`signal-row ${latestVoice ? "available" : ""}`}><span className="signal-icon">V</span><span><strong>Voice</strong><small>{latestVoice ? `${latestVoice.speaking_rate_wpm ? `${Math.round(latestVoice.speaking_rate_wpm)} words/min` : "Acoustic cues received"}` : "No recent voice analysis"}</small></span><i /></div>
                <div className={`signal-row ${latestFace ? "available" : ""}`}><span className="signal-icon">F</span><span><strong>Expression</strong><small>{latestFace ? `Estimated ${latestFace.measurements.expression_label}` : "No recent face estimate"}</small></span><i /></div>
              </div>
            </section>
            <div className="system-footnote"><span className="secure-mark">◈</span><span>Private to your local companion space</span></div>
          </aside>
        </main>
      )}

      {settingsOpen && user && (
        <MemorySettings
          user={user}
          companion={activeCompanion}
          relationship={activeRelationship}
          onClose={() => setSettingsOpen(false)}
          onUpdated={updateMemorySettings}
        />
      )}
    </div>
  );
}

export default App;