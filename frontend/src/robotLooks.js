const DEFAULT_LOOKS = {
  moss: {
    body: "rounded",
    finish: "matte",
    eyes: "soft",
    shell: "#242b27",
    secondary: "#111713",
    accent: "#20f58a",
    accessory: "antenna",
  },
  nova: {
    body: "organic",
    finish: "pearl",
    eyes: "gentle",
    shell: "#c2cbc5",
    secondary: "#75877b",
    accent: "#a4e7cd",
    accessory: "halo",
  },
  echo: {
    body: "angular",
    finish: "metallic",
    eyes: "confident",
    shell: "#454c47",
    secondary: "#202722",
    accent: "#ffac58",
    accessory: "ear-pads",
  },
  zen: {
    body: "geometric",
    finish: "satin",
    eyes: "calm",
    shell: "#89948d",
    secondary: "#303b34",
    accent: "#9adbb4",
    accessory: "halo",
  },
};

export function defaultLooks(companionId) {
  return { ...(DEFAULT_LOOKS[companionId] || DEFAULT_LOOKS.moss) };
}
