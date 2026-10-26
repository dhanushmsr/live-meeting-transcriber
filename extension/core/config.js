// Shared constants. Mirrors src/inkwake/config.py so the app and the extension behave the same.

export const LIVE_MODEL = "gemini-3.5-live-translate-preview";
export const REPORT_MODEL = "gemini-flash-latest";
export const FALLBACK_MODEL = "gemini-flash-lite-latest";
export const API_HOST = "generativelanguage.googleapis.com";
export const DEFAULT_CLOUD_URL = "https://inkwake-reports.inkwake.workers.dev";

export const LANG_CODES = {
  English: "en", Tamil: "ta", Hindi: "hi", Telugu: "te", Malayalam: "ml", Kannada: "kn", Bengali: "bn", Urdu: "ur",
  Spanish: "es", French: "fr", German: "de", Italian: "it", Portuguese: "pt", Russian: "ru", Arabic: "ar",
  Chinese: "zh", Japanese: "ja", Korean: "ko", Turkish: "tr", Indonesian: "id", Dutch: "nl",
};

export const DEFAULT_SETTINGS = {
  apiKey: "",
  name: "",
  targetLanguage: "English",
  translate: true,        // captions appear in targetLanguage
  captions: true,
  nameSpeakers: true,     // record the other side so voices can be told apart after the meeting
  shareOnline: false,     // opt-in: upload the finished report to the report server
  cloudUrl: DEFAULT_CLOUD_URL,
  micDeviceId: "",
};

export const REPORT_TTL_HOURS = 72;
