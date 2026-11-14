import "./RobotCharacter.css";
import { defaultLooks } from "./robotLooks";

function RobotCharacter({ companion, looks, compact = false, mood = "idle" }) {
  const appearance = { ...defaultLooks(companion?.companion_id), ...looks };
  const style = {
    "--robot-shell": appearance.shell,
    "--robot-secondary": appearance.secondary,
    "--robot-accent": appearance.accent,
  };
  const identity = companion?.companion_id || "moss";

  return (
    <div
      className={`robot-stage ${compact ? "robot-stage-compact" : ""}`}
      aria-label={`${companion?.name || "Moss"} robot companion`}
      role="img"
    >
      <div className={`robot robot-${identity} body-${appearance.body} finish-${appearance.finish} eye-${appearance.eyes} mood-${mood}`} style={style}>
        <div className="robot-ground-glow" />
        <div className={`robot-accessory accessory-${appearance.accessory}`} />
        <div className="robot-head">
          <div className="robot-ear robot-ear-left" />
          <div className="robot-ear robot-ear-right" />
          <div className="robot-faceplate">
            <span className="robot-brow robot-brow-left" />
            <span className="robot-brow robot-brow-right" />
            <span className="robot-eye robot-eye-left" />
            <span className="robot-eye robot-eye-right" />
            <span className="robot-mouth" />
          </div>
          <span className="robot-temple robot-temple-left" />
          <span className="robot-temple robot-temple-right" />
        </div>
        <div className="robot-neck" />
        <div className="robot-body">
          <div className="robot-shoulder robot-shoulder-left" />
          <div className="robot-shoulder robot-shoulder-right" />
          <div className="robot-chest">
            <span className="robot-core" />
            <span className="robot-chest-line" />
          </div>
          <div className="robot-arm robot-arm-left" />
          <div className="robot-arm robot-arm-right" />
        </div>
        <div className="robot-base">
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}

export default RobotCharacter;
