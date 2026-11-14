import { useEffect, useMemo, useRef, useState } from "react";
import RobotCharacter from "./RobotCharacter";
import { defaultLooks } from "./robotLooks";
import { apiJson, apiRequest, localStorageJson, saveLocalJson } from "./api";
import "./CompanionDesigner.css";

const SECTIONS = ["Identity", "Personality", "Looks", "Voice", "Advanced"];
const BODIES = ["rounded", "angular", "geometric", "organic", "minimalist"];
const FINISHES = ["matte", "metallic", "glossy", "pearl", "satin", "textured"];
const EYES = ["soft", "gentle", "confident", "calm", "wide"];
const ACCESSORIES = ["none", "antenna", "halo", "ear-pads"];
const BUILT_IN_IDS = ["moss", "nova", "echo", "zen"];
const VOICE_GUIDANCE = {
  moss: "Warm, relaxed and conversational; friendly and expressive.",
  nova: "Gentle, empathetic and reassuring; soft and thoughtful.",
  echo: "Energetic, confident and motivating; dynamic and encouraging.",
  zen: "Calm and grounded; measured, composed and unhurried.",
};

function localKey(userId, companionId) {
  return `mirrorling:companion-settings:${userId}:${companionId}`;
}

function voiceKey(userId, companionId) {
  return `mirrorling:voice-settings:${userId}:${companionId}`;
}

function makeDraft(companion, userId) {
  const stored = companion && typeof localStorage !== "undefined"
    ? localStorageJson(localKey(userId, companion.companion_id), {})
    : {};
  return {
    name: companion?.name || "",
    personality_description: companion?.personality_description || "",
    tagline: stored.tagline || "A companion shaped around your way of seeing the world.",
  };
}

function CompanionDesigner({ userId, companion, onSaved, onBack, onOpenMemory }) {
  const editingExisting = Boolean(companion);
  const editingCustom = companion?.kind === "custom";
  const [section, setSection] = useState("Identity");
  const [draft, setDraft] = useState(() => makeDraft(companion, userId));
  const [looks, setLooks] = useState(() => {
    const stored = companion && userId
      ? localStorageJson(localKey(userId, companion.companion_id), {})
      : {};
    return { ...defaultLooks(companion?.companion_id || "moss"), ...(stored.looks || {}) };
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [voiceCatalog, setVoiceCatalog] = useState(null);
  const [voiceLoading, setVoiceLoading] = useState(true);
  const [voiceError, setVoiceError] = useState("");
  const [voiceId, setVoiceId] = useState(() => {
    if (!userId || !companion) return null;
    const preference = localStorageJson(voiceKey(userId, companion.companion_id), {});
    return preference && Object.hasOwn(preference, "voice_id")
      ? preference.voice_id
      : null;
  });
  const [previewing, setPreviewing] = useState(false);
  const previewRef = useRef(null);
  const previewCompanion = useMemo(() => ({
    companion_id: companion?.companion_id || "custom-preview",
    name: draft.name || "Your companion",
  }), [companion?.companion_id, draft.name]);
  const selectedVoiceId = voiceId !== null
    ? voiceId
    : voiceCatalog?.default_voice_ids?.[companion?.companion_id] || "";
  const otherBuiltInVoiceIds = new Set(BUILT_IN_IDS
    .filter((id) => id !== companion?.companion_id)
    .map((id) => {
      const preference = userId ? localStorageJson(voiceKey(userId, id), {}) : {};
      return preference && Object.hasOwn(preference, "voice_id")
        ? preference.voice_id
        : voiceCatalog?.default_voice_ids?.[id];
    })
    .filter(Boolean));

  useEffect(() => {
    let active = true;
    apiJson("/speech/voices")
      .then((catalog) => { if (active) setVoiceCatalog(catalog); })
      .catch((loadError) => { if (active) setVoiceError(loadError.message); })
      .finally(() => { if (active) setVoiceLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => () => {
    if (!previewRef.current) return;
    previewRef.current.audio.pause();
    URL.revokeObjectURL(previewRef.current.url);
  }, []);

  function updateDraft(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function updateLook(field, value) {
    setLooks((current) => ({ ...current, [field]: value }));
  }

  function chooseVoice(nextVoiceId) {
    setVoiceId(nextVoiceId);
    setError("");
    setNotice("");
    if (!userId || !companion) return;
    const saved = saveLocalJson(voiceKey(userId, companion.companion_id), {
      voice_id: nextVoiceId,
    });
    if (!saved) setError("This browser could not save the voice selection locally.");
    else setNotice("Voice selection saved on this device.");
  }

  async function previewVoice() {
    if (!companion) {
      setVoiceError("Save this companion before assigning or previewing a voice.");
      return;
    }
    setVoiceError("");
    setPreviewing(true);
    try {
      if (previewRef.current) {
        previewRef.current.audio.pause();
        URL.revokeObjectURL(previewRef.current.url);
      }
      const audioBlob = await apiRequest("/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: `Hello, I'm ${draft.name || "your companion"}. It's good to hear from you.`,
          companion_id: companion.companion_id,
          voice_id: selectedVoiceId,
        }),
      });
      if (audioBlob.type === "audio/wav" && selectedVoiceId) {
        setNotice("ElevenLabs was unavailable for this preview; local Piper generated the audio.");
      }
      const url = URL.createObjectURL(audioBlob);
      const audio = new Audio(url);
      previewRef.current = { audio, url };
      audio.onended = () => {
        URL.revokeObjectURL(url);
        previewRef.current = null;
      };
      await audio.play();
    } catch (previewError) {
      setVoiceError(previewError.message || "Voice preview could not be played.");
    } finally {
      setPreviewing(false);
    }
  }

  function randomizeLooks() {
    const choose = (items) => items[Math.floor(Math.random() * items.length)];
    setLooks((current) => ({
      ...current,
      body: choose(BODIES),
      finish: choose(FINISHES),
      eyes: choose(EYES),
      accent: choose(["#20f58a", "#a4e7cd", "#ffac58", "#9adbb4", "#85d5ff"]),
      accessory: choose(ACCESSORIES),
    }));
  }

  function resetDesign() {
    setLooks(defaultLooks(companion?.companion_id || "moss"));
    setNotice("Appearance reset to its starting design.");
  }

  async function saveCompanion() {
    setError("");
    setNotice("");
    if (!editingExisting && (!draft.name.trim() || !draft.personality_description.trim())) {
      setError("Add a name and a personality description before saving.");
      setSection(!draft.name.trim() ? "Identity" : "Personality");
      return;
    }

    setSaving(true);
    try {
      if (!editingExisting) {
        const created = await apiJson(`/users/${userId}/companions`, "POST", {
          name: draft.name.trim(),
          personality_description: draft.personality_description.trim(),
        });
        saveLocalJson(localKey(userId, created.companion_id), {
          tagline: draft.tagline,
          looks,
        });
        await onSaved(created);
        return;
      }

      const saved = saveLocalJson(localKey(userId, companion.companion_id), {
        tagline: draft.tagline,
        looks,
      });
      if (!saved) throw new Error("This browser could not save the local design.");
      setNotice("Appearance and tagline saved on this device. Voice selection is stored separately.");
    } catch (saveError) {
      setError(saveError.message || "Could not save this companion.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="designer-screen">
      <header className="designer-heading">
        <div>
          <p className="eyebrow">COMPANION STUDIO / {editingExisting ? "EDIT LOCAL DESIGN" : "NEW COMPANION"}</p>
          <h1>{editingExisting ? "Tune a familiar presence." : "Shape a new presence."}</h1>
          <p className="designer-subtitle">Personality and appearance are separate. The robot preview changes as you design.</p>
        </div>
        <button className="quiet-button" onClick={onBack}>Back to companion</button>
      </header>

      <div className="designer-layout">
        <nav className="designer-nav" aria-label="Designer sections">
          <span className="nav-caption">CONFIGURATION</span>
          {SECTIONS.map((item, index) => (
            <button
              key={item}
              className={`designer-nav-item ${section === item ? "selected" : ""}`}
              onClick={() => setSection(item)}
            >
              <span className="designer-nav-index">0{index + 1}</span>
              <span>{item}</span>
            </button>
          ))}
          <div className="designer-nav-foot">
            <span className="local-dot" />
            <span>Looks are stored on this device</span>
          </div>
        </nav>

        <section className="designer-controls">
          {section === "Identity" && (
            <div className="control-section">
              <p className="eyebrow">01 / IDENTITY</p>
              <h2>Give them a name.</h2>
              <label className="field-label" htmlFor="companion-name">Companion name</label>
              <input
                id="companion-name"
                className="design-input"
                value={draft.name}
                onChange={(event) => updateDraft("name", event.target.value)}
                placeholder="e.g. Juniper"
                disabled={editingExisting}
                maxLength={60}
              />
              <label className="field-label" htmlFor="companion-tagline">Tagline <span>saved locally</span></label>
              <input
                id="companion-tagline"
                className="design-input"
                value={draft.tagline}
                onChange={(event) => updateDraft("tagline", event.target.value)}
                maxLength={120}
              />
              {editingExisting && (
                <p className="local-note">The current backend supports companion creation but not editing a saved companion name or personality. Those fields are loaded from your companion record and read-only here.</p>
              )}
            </div>
          )}

          {section === "Personality" && (
            <div className="control-section">
              <p className="eyebrow">02 / PERSONALITY</p>
              <h2>How should they feel?</h2>
              <p className="section-copy">This saved description is used by the companion-aware chat prompt.</p>
              <label className="field-label" htmlFor="personality-description">Personality description</label>
              <textarea
                id="personality-description"
                className="design-input design-textarea"
                value={draft.personality_description}
                onChange={(event) => updateDraft("personality_description", event.target.value)}
                placeholder="Describe how your companion listens, responds and brings energy to a conversation."
                disabled={editingExisting}
                maxLength={1200}
              />
              {editingExisting && <p className="local-note">Saved companion personalities cannot currently be edited through the backend API.</p>}
              {!editingExisting && (
                <div className="trait-ideas" aria-label="Personality prompts">
                  {[
                    ["Playful", "Enjoys gentle humour and shared fun."],
                    ["Empathetic", "Listens carefully before offering perspective."],
                    ["Motivating", "Encourages action and celebrates effort."],
                    ["Calm", "Keeps a grounded, reflective pace."],
                  ].map(([label, text]) => (
                    <button
                      type="button"
                      key={label}
                      className="trait-chip"
                      onClick={() => updateDraft("personality_description", `${draft.personality_description}${draft.personality_description ? " " : ""}${text}`)}
                    >{label}<span>+</span></button>
                  ))}
                </div>
              )}
            </div>
          )}

          {section === "Looks" && (
            <div className="control-section">
              <p className="eyebrow">03 / LOOKS</p>
              <h2>Make them unmistakable.</h2>
              <div className="control-grid">
                <label className="control-label">Body shape
                  <select value={looks.body} onChange={(event) => updateLook("body", event.target.value)}>
                    {BODIES.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className="control-label">Shell finish
                  <select value={looks.finish} onChange={(event) => updateLook("finish", event.target.value)}>
                    {FINISHES.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className="control-label">Eye style
                  <select value={looks.eyes} onChange={(event) => updateLook("eyes", event.target.value)}>
                    {EYES.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
                <label className="control-label">Accessory
                  <select value={looks.accessory} onChange={(event) => updateLook("accessory", event.target.value)}>
                    {ACCESSORIES.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
              </div>
              <div className="color-controls">
                {[["Shell", "shell"], ["Trim", "secondary"], ["Light", "accent"]].map(([label, field]) => (
                  <label key={field} className="color-control">
                    <span className="color-swatch" style={{ "--swatch": looks[field] }} />
                    <span>{label}</span>
                    <input type="color" value={looks[field]} onChange={(event) => updateLook(field, event.target.value)} aria-label={`${label} colour`} />
                  </label>
                ))}
              </div>
              <p className="local-note">Appearance is saved in this browser and does not change personality or backend memory.</p>
            </div>
          )}

          {section === "Voice" && (
            <div className="control-section">
              <p className="eyebrow">04 / VOICE</p>
              <h2>Choose their voice.</h2>
              <p className="section-copy">Voice is an independent setting and does not change personality, appearance or relationship history.</p>
              {!companion ? (
                <p className="local-note">Save this companion first, then return here to assign a voice.</p>
              ) : (
                <>
                  <label className="field-label" htmlFor="companion-voice">Voice for {draft.name || "this companion"}</label>
                  <p className="voice-guidance">{VOICE_GUIDANCE[companion.companion_id] || "Choose a voice that suits this companion."}</p>
                  <select
                    id="companion-voice"
                    className="design-input voice-select"
                    value={selectedVoiceId}
                    onChange={(event) => chooseVoice(event.target.value)}
                      disabled={voiceLoading}
                  >
                    <option value="">Use local Piper voice</option>
                    {selectedVoiceId && !voiceCatalog?.voices?.some((voice) => voice.voice_id === selectedVoiceId) && (
                      <option value={selectedVoiceId}>Configured voice ({selectedVoiceId})</option>
                    )}
                    {(voiceCatalog?.voices || []).map((voice) => {
                      const labels = Object.values(voice.labels || {}).filter(Boolean).join(" · ");
                      const details = [voice.category, labels, voice.description].filter(Boolean).join(" · ");
                      return <option key={voice.voice_id} value={voice.voice_id} disabled={BUILT_IN_IDS.includes(companion.companion_id) && otherBuiltInVoiceIds.has(voice.voice_id)}>{voice.name}{details ? ` — ${details}` : ""}</option>;
                    })}
                  </select>
                  <div className="voice-readout">
                    <span className="voice-wave">〰</span>
                    <div>
                      <strong>{voiceCatalog?.voices?.find((voice) => voice.voice_id === selectedVoiceId)?.name || (selectedVoiceId ? "Configured ElevenLabs voice" : "Kusal · Piper")}</strong>
                      <small>{selectedVoiceId ? "Selected for this companion" : "Local Piper fallback selected"}</small>
                    </div>
                    <span className="fixed-label">{selectedVoiceId ? "SELECTED" : "LOCAL"}</span>
                  </div>
                  {BUILT_IN_IDS.includes(companion.companion_id) && selectedVoiceId && otherBuiltInVoiceIds.has(selectedVoiceId) && <p className="local-note">This voice is also assigned to another built-in companion. Choose a different voice to keep their identities distinct.</p>}
                  <div className="voice-actions">
                    <button className="quiet-button" onClick={previewVoice} disabled={previewing || voiceLoading}>
                      {previewing ? "Preparing preview…" : "Preview voice"}
                    </button>
                    <span className="voice-storage-note">Saved on this device for this user and companion</span>
                  </div>
                </>
              )}
              {voiceLoading && <p className="local-note">Loading available voices…</p>}
              {!voiceLoading && voiceCatalog?.provider === "piper" && <p className="local-note">Piper is selected as the speech provider. ElevenLabs voices will not be used.</p>}
              {!voiceLoading && voiceCatalog?.provider === "elevenlabs" && !voiceCatalog?.elevenlabs_configured && <p className="local-note">ElevenLabs is not configured. Speech uses local Piper until an API key is added.</p>}
              {!voiceLoading && voiceError && <p className="local-note">Available voices could not be loaded. Piper remains available as a local speech provider.</p>}
              {voiceError && <p className="form-error" role="alert">{voiceError}</p>}
            </div>
          )}

          {section === "Advanced" && (
            <div className="control-section">
              <p className="eyebrow">05 / ADVANCED</p>
              <h2>Memory, on your terms.</h2>
              <p className="section-copy">Memory permissions are configured per user and per companion relationship.</p>
              <button className="text-action" onClick={onOpenMemory}>Open memory settings <span>↗</span></button>
              <div className="advanced-note"><span className="local-dot" /> The backend stores personality descriptions. Body design and tagline stay on this device.</div>
            </div>
          )}

          {error && <p className="form-error" role="alert">{error}</p>}
          {notice && <p className="form-notice" role="status">{notice}</p>}
          <div className="designer-actions">
            <button className="quiet-button" onClick={randomizeLooks}>Randomise look</button>
            <button className="quiet-button" onClick={resetDesign}>Reset look</button>
            <button className="primary-button" onClick={saveCompanion} disabled={saving}>
              {saving ? "Saving…" : editingExisting ? "Save local design" : "Save companion"}
            </button>
          </div>
        </section>

        <aside className="preview-column">
          <div className="preview-head"><span>LIVE PREVIEW</span><span className="live-dot" /></div>
          <div className="preview-stage">
            <div className="preview-grid" />
            <RobotCharacter companion={previewCompanion} looks={looks} />
          </div>
          <div className="preview-caption">
            <div><h3>{draft.name || "Your companion"}</h3><p>{draft.tagline}</p></div>
            <span className="preview-id">{editingCustom ? "CUSTOM" : "DESIGN"}</span>
          </div>
        </aside>
      </div>
    </main>
  );
}

export default CompanionDesigner;
