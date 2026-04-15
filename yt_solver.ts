/**
 * yt_solver.ts — YouTube URL resolver for Advanced-Leech-Bot
 *
 * Usage:
 *   deno run --allow-run --allow-env yt_solver.ts <youtube_url> [quality]
 *
 * quality: "best" (default) | "1080" | "720" | "480" | "360" | "audio"
 *
 * Output (stdout, JSON):
 *   { ok: true,  title, filename, url, audio_url?, ext, filesize?, needs_mux }
 *   { ok: false, error }
 *
 * needs_mux=true  → url=video stream, audio_url=audio stream — caller must mux
 * needs_mux=false → url is a single combined file, audio_url is undefined
 */

// ── Types ──────────────────────────────────────────────────────────────────────

interface YtFormat {
  format_id:   string;
  url:         string;
  ext:         string;
  vcodec:      string;
  acodec:      string;
  height:      number | null;
  tbr:         number | null;
  filesize:    number | null;
  filesize_approx: number | null;
  protocol:    string;
  fragments?:  unknown[];
}

interface YtInfo {
  title:        string;
  ext:          string;
  formats:      YtFormat[];
  url?:         string;   // present when there's only one format
  filesize?:    number;
  requested_formats?: YtFormat[];
  _filename?:   string;
  webpage_url:  string;
}

interface SolverResult {
  ok:         boolean;
  title?:     string;
  filename?:  string;
  url?:       string;
  audio_url?: string;
  ext?:       string;
  filesize?:  number;
  needs_mux?: boolean;
  error?:     string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function isDirectUrl(fmt: YtFormat): boolean {
  // Skip fragmented/DASH/HLS streams — aria2 can't reassemble them
  if (fmt.protocol && (fmt.protocol.includes("m3u8") || fmt.protocol.includes("dash"))) return false;
  if (fmt.fragments && fmt.fragments.length > 0) return false;
  return fmt.url.startsWith("http");
}

function hasBothStreams(fmt: YtFormat): boolean {
  return fmt.vcodec !== "none" && fmt.acodec !== "none";
}

function hasVideoOnly(fmt: YtFormat): boolean {
  return fmt.vcodec !== "none" && fmt.acodec === "none";
}

function hasAudioOnly(fmt: YtFormat): boolean {
  return fmt.vcodec === "none" && fmt.acodec !== "none";
}

function fsize(fmt: YtFormat): number {
  return fmt.filesize ?? fmt.filesize_approx ?? 0;
}

function sanitizeFilename(name: string): string {
  return name.replace(/[\\/:*?"<>|]/g, "_").replace(/\s+/g, " ").trim().slice(0, 200);
}

// ── Format selector ────────────────────────────────────────────────────────────

function selectFormats(
  formats: YtFormat[],
  quality: string,
): { video: YtFormat; audio: YtFormat | null; needsMux: boolean } | null {

  const direct = formats.filter(isDirectUrl);
  if (direct.length === 0) return null;

  const maxHeight = quality === "audio" ? 0
    : quality === "best"               ? Infinity
    : parseInt(quality, 10) || Infinity;

  // ── 1. Try to find a single combined stream ────────────────────────────────
  if (quality !== "audio") {
    const combined = direct
      .filter(hasBothStreams)
      .filter(f => !maxHeight || (f.height ?? Infinity) <= maxHeight)
      .sort((a, b) => (b.height ?? 0) - (a.height ?? 0) || (b.tbr ?? 0) - (a.tbr ?? 0));

    if (combined.length > 0) {
      return { video: combined[0], audio: null, needsMux: false };
    }
  }

  // ── 2. Separate video + audio streams (needs muxing) ──────────────────────
  if (quality !== "audio") {
    const videoStreams = direct
      .filter(hasVideoOnly)
      .filter(f => !maxHeight || (f.height ?? Infinity) <= maxHeight)
      .sort((a, b) => (b.height ?? 0) - (a.height ?? 0) || (b.tbr ?? 0) - (a.tbr ?? 0));

    const audioStreams = direct
      .filter(hasAudioOnly)
      .sort((a, b) => (b.tbr ?? 0) - (a.tbr ?? 0));

    if (videoStreams.length > 0 && audioStreams.length > 0) {
      return { video: videoStreams[0], audio: audioStreams[0], needsMux: true };
    }

    // video stream exists but no separate audio — return video-only
    if (videoStreams.length > 0) {
      return { video: videoStreams[0], audio: null, needsMux: false };
    }
  }

  // ── 3. Audio-only ──────────────────────────────────────────────────────────
  const audioStreams = direct
    .filter(hasAudioOnly)
    .sort((a, b) => (b.tbr ?? 0) - (a.tbr ?? 0));

  if (audioStreams.length > 0) {
    return { video: audioStreams[0], audio: null, needsMux: false };
  }

  return null;
}

// ── Main ───────────────────────────────────────────────────────────────────────

async function solve(ytUrl: string, quality: string): Promise<SolverResult> {
  const cmd = new Deno.Command("yt-dlp", {
    args: [
      "--dump-json",
      "--no-playlist",
      "--no-warnings",
      "--quiet",
      "--extractor-args", "youtube:player_client=android,web",
      ytUrl,
    ],
    stdout: "piped",
    stderr: "piped",
  });

  let proc: Deno.ChildProcess;
  try {
    proc = cmd.spawn();
  } catch {
    return { ok: false, error: "yt-dlp not found — install it and make sure it is in PATH" };
  }

  const { code, stdout, stderr } = await proc.output();

  if (code !== 0) {
    const errText = new TextDecoder().decode(stderr).trim();
    return { ok: false, error: errText || `yt-dlp exited with code ${code}` };
  }

  let info: YtInfo;
  try {
    info = JSON.parse(new TextDecoder().decode(stdout));
  } catch {
    return { ok: false, error: "Failed to parse yt-dlp JSON output" };
  }

  // Some extractors return a single url at top level with no formats array
  if (!info.formats || info.formats.length === 0) {
    if (info.url) {
      const ext  = info.ext || "mp4";
      const name = sanitizeFilename(info.title || "video") + "." + ext;
      return {
        ok: true, title: info.title, filename: name,
        url: info.url, ext, filesize: info.filesize,
        needs_mux: false,
      };
    }
    return { ok: false, error: "No downloadable formats found" };
  }

  const selected = selectFormats(info.formats, quality);
  if (!selected) {
    return { ok: false, error: "No direct (non-fragmented) formats available for this video" };
  }

  const { video, audio, needsMux } = selected;

  const ext      = needsMux ? "mkv" : (video.ext || "mp4");
  const title    = info.title || "video";
  const filename = sanitizeFilename(title) + "." + ext;
  const filesize = needsMux
    ? (fsize(video) + (audio ? fsize(audio) : 0)) || undefined
    : fsize(video) || undefined;

  return {
    ok:        true,
    title,
    filename,
    url:       video.url,
    audio_url: audio?.url,
    ext,
    filesize,
    needs_mux: needsMux,
  };
}

// ── Entry point ────────────────────────────────────────────────────────────────

const [url, quality = "best"] = Deno.args;

if (!url) {
  console.log(JSON.stringify({ ok: false, error: "No URL provided" }));
  Deno.exit(1);
}

const result = await solve(url, quality);
console.log(JSON.stringify(result));
Deno.exit(result.ok ? 0 : 1);
