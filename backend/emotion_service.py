from io import BytesIO
from threading import Lock

import numpy as np
from PIL import Image, UnidentifiedImageError

from emotiefflib.facial_analysis import EmotiEffLibRecognizer


# Load the pretrained model once when the backend starts.
recognizer = EmotiEffLibRecognizer(
    engine="onnx",
    model_name="enet_b0_8_va_mtl"
)

# Prevent simultaneous inference calls from using the recognizer.
inference_lock = Lock()


def analyze_face_image(image_bytes: bytes) -> dict:
    """
    Decode an uploaded face image and run EmotiEffLib inference.
    Images are processed in memory and are not saved.
    """

    try:
        with Image.open(BytesIO(image_bytes)) as image:

            width, height = image.size

            if width < 48 or height < 48:
                raise ValueError("Face image is too small.")

            if width * height > 4_000_000:
                raise ValueError("Image dimensions are too large.")

            # EmotiEffLib expects a NumPy image array.
            face_image = np.asarray(
                image.convert("RGB"),
                dtype=np.uint8
            )

    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Invalid image received.") from error

    with inference_lock:
        labels, scores = recognizer.predict_emotions(
            face_image,
            logits=False
        )

    values = scores[0]

    if len(values) != 10:
        raise RuntimeError("Unexpected model output format.")

    emotion_scores = {
        recognizer.idx_to_emotion_class[index]: round(
            float(values[index]), 4
        )
        for index in range(8)
    }

    return {
        "expression_label": labels[0],
        "expression_scores": emotion_scores,
        "valence": round(float(values[8]), 4),
        "arousal": round(float(values[9]), 4)
    }