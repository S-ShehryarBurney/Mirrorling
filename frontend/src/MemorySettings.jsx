import { useEffect, useState } from "react";
import { apiJson } from "./api";

const MEMORY_MODES = [
  {
    value: "remember_useful_things",
    label: "Remember useful things",
    detail: "Save useful, non-sensitive memories automatically.",
  },
  {
    value: "ask_before_remembering",
    label: "Ask before remembering",
    detail: "Keep approved memories; ask before saving new ones.",
  },
  {
    value: "dont_remember",
    label: "Don't remember",
    detail: "Do not save or retrieve persistent memories.",
  },
];

function MemorySettings({ user, companion, relationship, onClose, onUpdated }) {
  const [globalMode, setGlobalMode] = useState(() => user?.memory_preferences?.default_mode || "ask_before_remembering");
  const [relationshipMode, setRelationshipMode] = useState(() => relationship?.memory_mode_override ?? "inherit");
  const [candidates, setCandidates] = useState([]);
  const [shareCandidates, setShareCandidates] = useState({});
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const userId = user?.user_id;
  const companionId = companion?.companion_id;

  const candidatePath = userId
    ? companionId
      ? `/users/${userId}/companions/${companionId}/familiarity-candidates`
      : `/users/${userId}/familiarity-candidates`
    : null;

  useEffect(() => {
    if (!candidatePath) return undefined;
    let active = true;
    apiJson(candidatePath)
      .then((result) => { if (active) setCandidates(result); })
      .catch((loadError) => { if (active) setError(loadError.message); })
      .finally(() => { if (active) setLoadingCandidates(false); });
    return () => { active = false; };
  }, [candidatePath]);

  async function loadCandidates() {
    if (!candidatePath) return;
    setLoadingCandidates(true);
    try {
      setCandidates(await apiJson(candidatePath));
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoadingCandidates(false);
    }
  }

  async function savePreferences() {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const currentGlobalMode = user?.memory_preferences?.default_mode || "ask_before_remembering";
      const updatedUser = globalMode === currentGlobalMode
        ? user
        : await apiJson(`/users/${userId}/memory-preference`, "PATCH", {
            default_mode: globalMode,
          });
      let updatedRelationship = relationship;
      const nextOverride = relationshipMode === "inherit" ? null : relationshipMode;
      if (
        companionId
        && relationship
        && nextOverride !== (relationship.memory_mode_override ?? null)
      ) {
        updatedRelationship = await apiJson(
          `/users/${userId}/relationships/${companionId}/memory-mode`,
          "PATCH",
          { memory_mode_override: nextOverride },
        );
      }
      onUpdated?.(updatedUser, updatedRelationship);
      setNotice("Memory preferences saved.");
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  }

  async function resolveCandidate(candidateId, action) {
    setError("");
    try {
      const path = `/users/${userId}/familiarity-candidates/${candidateId}/${action}`;
      const shared = Boolean(shareCandidates[candidateId]);
      await apiJson(path, "POST", action === "confirm" ? { share_across_companions: shared } : undefined);
      setNotice(action === "confirm"
        ? shared ? "Pattern confirmed and shared across companions." : "Pattern confirmed for this companion."
        : "Pattern rejected.");
      await loadCandidates();
    } catch (actionError) {
      setError(actionError.message);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="memory-modal" role="dialog" aria-modal="true" aria-labelledby="memory-title">
        <header className="modal-heading">
          <div><p className="eyebrow">YOUR PRIVACY</p><h2 id="memory-title">Memory settings</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="Close memory settings">×</button>
        </header>
        <section className="provider-disclosure" aria-label="Cloud processing">
          <strong>Cloud processing</strong>
          <p>When Gemini is selected, chat messages and the prompt context used for replies, including permitted memory, familiarity and perception estimates, are sent to Google. When ElevenLabs is selected, assistant replies and voice-preview text are sent there to generate speech. Microphone transcription stays local; raw recordings and camera images are not sent to either provider. User IDs are not sent. First Meeting state only shapes the prompt instructions when relevant.</p>
        </section>
        <div className="memory-setting-block">
          <label htmlFor="global-memory-mode">Default memory preference</label>
          <select id="global-memory-mode" value={globalMode} onChange={(event) => setGlobalMode(event.target.value)}>
            {MEMORY_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
          </select>
          <p>{MEMORY_MODES.find((mode) => mode.value === globalMode)?.detail}</p>
        </div>
        {companion && (
          <div className="memory-setting-block">
            <label htmlFor="relationship-memory-mode">{companion.name}'s preference</label>
            <select id="relationship-memory-mode" value={relationshipMode} onChange={(event) => setRelationshipMode(event.target.value)}>
              <option value="inherit">Use default preference</option>
              {MEMORY_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
            </select>
            <p>A companion-specific preference overrides your default.</p>
          </div>
        )}
        <div className="candidate-block">
          <div className="candidate-heading"><div><label>Familiarity patterns</label><p>Confirm only what feels accurate. Nothing is saved as a memory without your choice.</p></div><span className="candidate-count">{candidates.length}</span></div>
          {loadingCandidates ? <p className="empty-note">Checking for pending patterns…</p> : candidates.length === 0 ? <p className="empty-note">No pending patterns for {companion?.name || "your companions"}.</p> : (
            <div className="candidate-list">
              {candidates.map((candidate) => (
                <article className="candidate-item" key={candidate.candidate_id}>
                  <p>{candidate.observation_summary}</p>
                  <small>{candidate.evidence_count} observations · {candidate.distinct_day_count} separate days · tentative</small>
                  <label className="share-candidate-choice"><input type="checkbox" checked={Boolean(shareCandidates[candidate.candidate_id])} onChange={(event) => setShareCandidates((current) => ({ ...current, [candidate.candidate_id]: event.target.checked }))} /> Share with other companions</label>
                  <div className="candidate-actions">
                    <button className="quiet-button" onClick={() => resolveCandidate(candidate.candidate_id, "reject")}>Not accurate</button>
                    <button className="primary-button" onClick={() => resolveCandidate(candidate.candidate_id, "confirm")}>Confirm for {companion?.name || "companion"}</button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
        {error && <p className="form-error" role="alert">{error}</p>}
        {notice && <p className="form-notice" role="status">{notice}</p>}
        <footer className="modal-actions"><button className="quiet-button" onClick={onClose}>Close</button><button className="primary-button" onClick={savePreferences} disabled={saving}>{saving ? "Saving…" : "Save preferences"}</button></footer>
      </section>
    </div>
  );
}

export default MemorySettings;
