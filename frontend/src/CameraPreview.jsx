import { useEffect, useRef, useState } from "react";
import "./CameraPreview.css";
import FaceAnalysis from "./FaceAnalysis";

function CameraPreview({ onEmotionResult }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  const [cameraOn, setCameraOn] = useState(false);
  const [status, setStatus] = useState("Camera is off");

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("Camera access is unavailable in this browser.");
      return;
    }

    try {
      setStatus("Requesting camera access...");

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: "user",
        },
        audio: false,
      });

      streamRef.current = stream;

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }

      setCameraOn(true);
      setStatus("Camera is active");
    } catch (error) {
      console.error("Camera error:", error);

      setStatus(
        error.name === "NotAllowedError"
          ? "Camera permission denied."
          : error.name === "NotFoundError"
          ? "No camera detected."
          : "Could not access the camera."
      );
    }
  }

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop());

    streamRef.current = null;

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }

    setCameraOn(false);
    setStatus("Camera is off");
  }

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return (
    <section className="camera-panel">
      <div className="camera-header">
        <h3>Visual perception</h3>
        <span>{status}</span>
      </div>

      <div className="camera-preview">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
        />

        {!cameraOn && (
          <p>Camera preview unavailable</p>
        )}
      </div>

      <button
        className="camera-button"
        onClick={cameraOn ? stopCamera : startCamera}
      >
        {cameraOn ? "Turn camera off" : "Turn camera on"}
      </button>
      <FaceAnalysis
        videoRef={videoRef}
        cameraOn={cameraOn}
        onEmotionResult={onEmotionResult}
      />
    </section>
  );
}

export default CameraPreview;
