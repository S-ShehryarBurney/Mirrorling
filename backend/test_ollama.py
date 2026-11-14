from ollama_client import client, MODEL

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "user", "content": "Introduce yourself as Moss in one sentence."}
    ],
    max_tokens=150
)

print(response.choices[0].message.content)