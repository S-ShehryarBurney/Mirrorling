import { useEffect, useRef, useState } from "react";
import { FaceLandmarker, FilesetResolver } from "@mediapipe/tasks-vision";
import { apiUrl } from "./api";

function FaceAnalysis({ videoRef, cameraOn, onEmotionResult }) {
  const frameRef = useRef(null);

  const [analysisStatus, setAnalysisStatus] = useState("Camera is off");
  const [metrics, setMetrics] = useState(null);
  const [emotionResult, setEmotionResult] = useState(null);
  const [emotionStatus, setEmotionStatus] = useState("Waiting for camera");

  useEffect(() => {
    if (!cameraOn) {
      onEmotionResult?.(null);
      return;
    }

    let cancelled = false;
    let lastVideoTime = -1;
    let lastInferenceAt = 0;
    let lastUiUpdateAt = 0;
    let lastBackendRequestAt = 0;
    let backendRequestInFlight = false;
    let landmarkerInstance = null;
    let requestController = null;

    async function analyzeFaceCrop(video, face) {
      if (backendRequestInFlight) return;

      if (Date.now() - lastBackendRequestAt < 1000) return;

      const capturedAt = Date.now();
      backendRequestInFlight = true;
      lastBackendRequestAt = Date.now();

      try {
        const xs = face.map((point) => point.x * video.videoWidth);
        const ys = face.map((point) => point.y * video.videoHeight);

        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);

        // Create a square crop with some padding around the face.
        const faceWidth = maxX - minX;
        const faceHeight = maxY - minY;

        const side = Math.min(
          Math.max(faceWidth, faceHeight) * 1.3,
          video.videoWidth,
          video.videoHeight
        );

        if (side < 2) return;

        const centerX = (minX + maxX) / 2;
        const centerY = (minY + maxY) / 2;

        const sourceX = Math.max(
          0,
          Math.min(video.videoWidth - side, centerX - side / 2)
        );

        const sourceY = Math.max(
          0,
          Math.min(video.videoHeight - side, centerY - side / 2)
        );

        const canvas = document.createElement("canvas");
        canvas.width = 224;
        canvas.height = 224;

        const context = canvas.getContext("2d");

        if (!context) {
          throw new Error("Could not create image canvas.");
        }

        context.drawImage(
          video,
          sourceX,
          sourceY,
          side,
          side,
          0,
          0,
          224,
          224
        );

        const imageBlob = await new Promise((resolve, reject) => {
          canvas.toBlob(
            (blob) => {
              if (blob) resolve(blob);
              else reject(new Error("Could not create face image."));
            },
            "image/jpeg",
            0.85
          );
        });

        if (cancelled) return;

        const formData = new FormData();
        formData.append("file", imageBlob, "face.jpg");

        requestController = new AbortController();

        setEmotionStatus("Analysing facial expression...");

        const response = await fetch(apiUrl("/analyze-face"), {
          method: "POST",
          body: formData,
          signal: requestController.signal,
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || "Facial analysis request failed.");
        }

        if (!cancelled) {
          setEmotionResult(data);
          onEmotionResult?.({ capturedAt, measurements: data });
          setEmotionStatus("Analysis successful");
        }
      } catch (error) {
        if (!cancelled && error.name !== "AbortError") {
          console.error("EmotiEffLib error:", error);
          setEmotionStatus(`Emotion analysis failed: ${error.message}`);
        }
      } finally {
        backendRequestInFlight = false;
        requestController = null;
      }
    }

    async function startAnalysis() {
      try {
        setAnalysisStatus("Loading facial analysis...");

        const vision = await FilesetResolver.forVisionTasks(
          "/mediapipe/wasm"
        );

        const landmarker = await FaceLandmarker.createFromOptions(vision, {
          baseOptions: {
            modelAssetPath: "/models/face_landmarker.task",
          },
          runningMode: "VIDEO",
          numFaces: 1,
          outputFaceBlendshapes: true,
        });

        landmarkerInstance = landmarker;

        if (cancelled) {
          landmarker.close();
          landmarkerInstance = null;
          return;
        }

        setAnalysisStatus("Detector ready. Looking for a face...");
        setEmotionStatus("Waiting for a face...");

        function detectFrame() {
          if (cancelled) return;

          const video = videoRef.current;

          if (
            video &&
            video.readyState >= 2 &&
            video.currentTime !== lastVideoTime
          ) {
            const now = performance.now();

            // Limit MediaPipe inference to approximately 6-7 FPS.
            if (now - lastInferenceAt >= 150) {
              lastInferenceAt = now;
              lastVideoTime = video.currentTime;

              try {
                const result = landmarker.detectForVideo(video, now);
                const face = result.faceLandmarks?.[0];

                if (face) {
                  const categories =
                    result.faceBlendshapes?.[0]?.categories ?? [];

                  const strongest = [...categories]
                    .sort((a, b) => b.score - a.score)
                    .slice(0, 3)
                    .map(({ categoryName, score }) => ({
                      name: categoryName,
                      score: Math.round(score * 100),
                    }));

                  setAnalysisStatus("Face detected");

                  if (now - lastUiUpdateAt >= 500) {
                    setMetrics({
                      landmarkCount: face.length,
                      blendshapeCount: categories.length,
                      strongest,
                    });

                    lastUiUpdateAt = now;
                  }

                  // Send an occasional face crop to the backend.
                  analyzeFaceCrop(video, face);
                } else {
                  setAnalysisStatus("No face detected");
                  setEmotionStatus("Waiting for a face...");
                  setEmotionResult(null);

                  if (now - lastUiUpdateAt >= 500) {
                    setMetrics(null);
                    lastUiUpdateAt = now;
                  }
                }
              } catch (error) {
                console.error("MediaPipe detection error:", error);
                setAnalysisStatus(`Detection error: ${error.message}`);
                return;
              }
            }
          }

          frameRef.current = requestAnimationFrame(detectFrame);
        }

        detectFrame();
      } catch (error) {
        console.error("MediaPipe initialization error:", error);

        if (!cancelled) {
          setAnalysisStatus(`Facial analysis error: ${error.message}`);
        }
      }
    }

    startAnalysis();

    return () => {
      cancelled = true;

      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
      }

      frameRef.current = null;

      requestController?.abort();

      landmarkerInstance?.close();
      landmarkerInstance = null;
    };
  }, [cameraOn, videoRef, onEmotionResult]);

  const visibleAnalysisStatus = cameraOn ? analysisStatus : "Turn on the camera to begin.";
  const visibleEmotionStatus = cameraOn ? emotionStatus : "Waiting for camera";
  const visibleMetrics = cameraOn ? metrics : null;
  const visibleEmotionResult = cameraOn ? emotionResult : null;
  const expressionScore =
    visibleEmotionResult?.expression_scores?.[visibleEmotionResult?.expression_label];

  return (
    <div className="face-analysis">
      <p>
        <strong>Facial analysis:</strong> {visibleAnalysisStatus}
      </p>

      {visibleMetrics && (
        <p>
          {visibleMetrics.landmarkCount} landmarks detected ·{" "}
          {visibleMetrics.blendshapeCount} blendshape signals
          {visibleMetrics.strongest.length > 0 && (
            <>
              {" · "}
              {visibleMetrics.strongest
                .map((item) => `${item.name}: ${item.score}%`)
                .join(", ")}
            </>
          )}
        </p>
      )}

      <p>
        <strong>Expression analysis:</strong> {visibleEmotionStatus}
      </p>

      {visibleEmotionResult && (
        <>
          <p>
            Estimated expression:{" "}
            <strong>{visibleEmotionResult.expression_label}</strong>
            {expressionScore !== undefined &&
              ` (${Math.round(expressionScore * 100)}% model score)`}
          </p>

          <p>
            Valence: {visibleEmotionResult.valence.toFixed(3)} · Arousal:{" "}
            {visibleEmotionResult.arousal.toFixed(3)}
          </p>
        </>
      )}
    </div>
  );
}

export default FaceAnalysis;
