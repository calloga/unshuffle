#include "unshuffle_extractor.h"
#include "fft_utils.h"

#define DR_WAV_IMPLEMENTATION
#include "dr_wav.h"
#define DR_MP3_IMPLEMENTATION
#include "dr_mp3.h"
#define DR_FLAC_IMPLEMENTATION
#include "dr_flac.h"
#define STB_VORBIS_HEADER_ONLY
#include "stb_vorbis.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdio.h>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

const char *FEATURE_SCHEMA[] = {
    "brightness",
    "percussivity",
    "fft_register",
    "zcr",
    "decay",
    "chroma_0",
    "chroma_1",
    "chroma_2",
    "chroma_3",
    "chroma_4",
    "chroma_5",
    "chroma_6",
    "chroma_7",
    "chroma_8",
    "chroma_9",
    "chroma_10",
    "chroma_11",
    "active_duration",
    "loopiness_score",
    "transient_tail_score",
};

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
std::wstring utf8_to_utf16(const std::string &utf8) {
  if (utf8.empty())
    return std::wstring();
  int size_needed =
      MultiByteToWideChar(CP_UTF8, 0, &utf8[0], (int)utf8.size(), NULL, 0);
  std::wstring strTo(size_needed, 0);
  MultiByteToWideChar(CP_UTF8, 0, &utf8[0], (int)utf8.size(), &strTo[0],
                      size_needed);
  return strTo;
}
#else
std::wstring utf8_to_utf16(const std::string &s) {
  return std::wstring(s.begin(), s.end());
}
#endif

std::string shell_quote(const std::string &value) {
#ifdef _WIN32
  std::string escaped = "\"";
  for (char c : value) {
    if (c == '"')
      escaped += "\\\"";
    else
      escaped += c;
  }
  escaped += "\"";
  return escaped;
#else
  std::string escaped = "'";
  for (char c : value) {
    if (c == '\'')
      escaped += "'\\''";
    else
      escaped += c;
  }
  escaped += "'";
  return escaped;
#endif
}

std::string path_to_utf8(const fs::path &path) {
#ifdef _WIN32
  return path.u8string();
#else
  return path.string();
#endif
}

std::string find_ffmpeg_executable() {
  const char *env_path = std::getenv("UNSHUFFLE_FFMPEG_PATH");
  if (env_path && env_path[0] != '\0')
    return std::string(env_path);
#ifdef _WIN32
  fs::path bundled = fs::current_path() / "bin" / "windows" / "ffmpeg.exe";
  if (fs::exists(bundled))
    return path_to_utf8(bundled);
  bundled = fs::current_path() / "bin" / "ffmpeg.exe";
  if (fs::exists(bundled))
    return path_to_utf8(bundled);
#else
  fs::path bundled = fs::current_path() / "bin" / "ffmpeg";
  if (fs::exists(bundled))
    return path_to_utf8(bundled);
#endif
  return "ffmpeg";
}

#ifdef _WIN32
std::wstring windows_quote_arg(const std::wstring &value) {
  if (value.empty())
    return L"\"\"";

  bool needs_quotes = false;
  for (wchar_t c : value) {
    if (c == L' ' || c == L'\t' || c == L'"') {
      needs_quotes = true;
      break;
    }
  }
  if (!needs_quotes)
    return value;

  std::wstring quoted = L"\"";
  size_t backslashes = 0;
  for (wchar_t c : value) {
    if (c == L'\\') {
      backslashes++;
      continue;
    }
    if (c == L'"') {
      quoted.append(backslashes * 2 + 1, L'\\');
      quoted += c;
      backslashes = 0;
      continue;
    }
    quoted.append(backslashes, L'\\');
    backslashes = 0;
    quoted += c;
  }
  quoted.append(backslashes * 2, L'\\');
  quoted += L"\"";
  return quoted;
}

bool decode_with_ffmpeg_windows(const fs::path &path,
                                std::vector<float> &mono_samples) {
  SECURITY_ATTRIBUTES security_attrs;
  security_attrs.nLength = sizeof(SECURITY_ATTRIBUTES);
  security_attrs.bInheritHandle = TRUE;
  security_attrs.lpSecurityDescriptor = NULL;

  HANDLE read_pipe = NULL;
  HANDLE write_pipe = NULL;
  if (!CreatePipe(&read_pipe, &write_pipe, &security_attrs, 0)) {
    std::cerr << "ffmpeg: Failed to start\n";
    return false;
  }
  if (!SetHandleInformation(read_pipe, HANDLE_FLAG_INHERIT, 0)) {
    CloseHandle(read_pipe);
    CloseHandle(write_pipe);
    std::cerr << "ffmpeg: Failed to start\n";
    return false;
  }

  std::wstring ffmpeg = utf8_to_utf16(find_ffmpeg_executable());
  std::wstring command =
      windows_quote_arg(ffmpeg) + L" -v error -nostdin -i " +
      windows_quote_arg(path.wstring()) +
      L" -t 60 -ac 1 -ar 44100 -f f32le pipe:1";

  STARTUPINFOW startup_info;
  ZeroMemory(&startup_info, sizeof(startup_info));
  startup_info.cb = sizeof(startup_info);
  startup_info.dwFlags = STARTF_USESTDHANDLES;
  startup_info.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
  startup_info.hStdOutput = write_pipe;
  startup_info.hStdError = GetStdHandle(STD_ERROR_HANDLE);

  PROCESS_INFORMATION process_info;
  ZeroMemory(&process_info, sizeof(process_info));

  std::vector<wchar_t> command_buffer(command.begin(), command.end());
  command_buffer.push_back(L'\0');

  BOOL started = CreateProcessW(NULL, command_buffer.data(), NULL, NULL, TRUE,
                                CREATE_NO_WINDOW, NULL, NULL, &startup_info,
                                &process_info);
  CloseHandle(write_pipe);

  if (!started) {
    CloseHandle(read_pipe);
    std::cerr << "ffmpeg: Failed to start\n";
    return false;
  }

  std::vector<char> bytes;
  char buffer[8192];
  while (true) {
    DWORD bytes_read = 0;
    BOOL ok = ReadFile(read_pipe, buffer, sizeof(buffer), &bytes_read, NULL);
    if (!ok || bytes_read == 0)
      break;
    bytes.insert(bytes.end(), buffer, buffer + bytes_read);
  }

  CloseHandle(read_pipe);
  WaitForSingleObject(process_info.hProcess, INFINITE);

  DWORD exit_code = 1;
  GetExitCodeProcess(process_info.hProcess, &exit_code);
  CloseHandle(process_info.hThread);
  CloseHandle(process_info.hProcess);

  if (exit_code != 0 || bytes.empty() || bytes.size() % sizeof(float) != 0) {
    std::cerr << "ffmpeg: Failed to decode file\n";
    return false;
  }

  mono_samples.resize(bytes.size() / sizeof(float));
  std::memcpy(mono_samples.data(), bytes.data(), bytes.size());
  return true;
}
#endif

bool decode_with_ffmpeg(const fs::path &path, std::vector<float> &mono_samples,
                        int &sampleRate, int &channels) {
  sampleRate = 44100;
  channels = 1;
#ifdef _WIN32
  return decode_with_ffmpeg_windows(path, mono_samples);
#else
  std::string command = shell_quote(find_ffmpeg_executable()) +
                        " -v error -nostdin -i " +
                        shell_quote(path_to_utf8(path)) +
                        " -t 60 -ac 1 -ar 44100 -f f32le pipe:1";
  FILE *pipe = popen(command.c_str(), "r");
  if (!pipe) {
    std::cerr << "ffmpeg: Failed to start\n";
    return false;
  }

  std::vector<char> bytes;
  char buffer[8192];
  while (true) {
    size_t read = fread(buffer, 1, sizeof(buffer), pipe);
    if (read > 0)
      bytes.insert(bytes.end(), buffer, buffer + read);
    if (read < sizeof(buffer))
      break;
  }
  int exit_code = pclose(pipe);
  if (exit_code != 0 || bytes.empty() || bytes.size() % sizeof(float) != 0) {
    std::cerr << "ffmpeg: Failed to decode file\n";
    return false;
  }

  mono_samples.resize(bytes.size() / sizeof(float));
  std::memcpy(mono_samples.data(), bytes.data(), bytes.size());
  return true;
#endif
}

int health_check(std::vector<float> &samples) {
  if (samples.empty()) {
    std::cerr << "File is empty\n";
    return 1;
  }

  float rms = 0;
  for (size_t i = 0; i < samples.size(); i++) {
    if (!std::isfinite(samples[i])) {
      std::cerr << "Audio contains NaN or infinite values\n";
      return 1;
    }
    if (std::abs(samples[i]) > 10.0f) {
      std::cerr << "Severe audio clipping detected\n";
      return 1;
    }
    rms += samples[i] * samples[i];
  }

  float rms_val = rms / (float)samples.size();
  if (rms_val < 1e-10f) {
    std::cerr << "File is silent (RMS: " << rms_val << ")\n";
    return 1;
  }

  if (samples.size() < 512) {
    std::cerr << "Audio too short to analyze\n";
    return 1;
  }

  return 0;
}

int main(int argc, char *argv[]) {
  for (int i = 1; i < argc; i++) {
    std::string arg = argv[i];
    if (arg == "--help" || arg == "-h") {
      std::cout << "Usage: unshuffle_extractor --file <audio_file_path>\n";
      return 0;
    }
    if (arg == "--version") {
      std::cout << "unshuffle_extractor 1.0.0\n";
      return 0;
    }
  }

  std::wstring wfilepath;

#ifdef _WIN32
  int w_argc;
  wchar_t **w_argv = CommandLineToArgvW(GetCommandLineW(), &w_argc);
  if (w_argv) {
    for (int i = 1; i < w_argc; i++) {
      if (std::wstring(w_argv[i]) == L"--file" && (i + 1) < w_argc) {
        wfilepath = w_argv[i + 1];
      }
    }
    LocalFree(w_argv);
  }
#else
  std::string filepath;
  if (argc < 2) {
    std::cerr << "Usage: unshuffle_extractor --file <audio_file_path>\n";
    return 1;
  }

  for (int i = 1; i < argc; i++) {
    if (std::string(argv[i]) == "--file" && (i + 1) < argc) {
      filepath = argv[i + 1];
    }
  }

  if (filepath.empty()) {
    std::cerr << "No file provided\n";
    return 1;
  }

  wfilepath = utf8_to_utf16(filepath);
#endif

  if (wfilepath.empty()) {
    std::cerr << "No file path provided or failed to parse arguments\n";
    return 1;
  }

  std::wstring wpath;

#ifdef _WIN32
  try {
    fs::path p(wfilepath);
    fs::path abs_p = fs::absolute(p);
    std::wstring wstr = abs_p.wstring();

    std::replace(wstr.begin(), wstr.end(), L'/', L'\\');

    if (wstr.length() > 240 && wstr.find(L"\\\\?\\") != 0) {
      wpath = L"\\\\?\\" + wstr;
    } else {
      wpath = wstr;
    }
  } catch (...) {
    wpath = wfilepath;
  }
#else
  wpath = wfilepath;
#endif

  std::string lower_path;
  for (wchar_t c : wfilepath) {
    if (c < 128)
      lower_path += (char)std::tolower((unsigned char)c);
    else
      lower_path += '?';
  }

  std::vector<float> mono_samples;
  int sampleRate = 0;
  int channels = 0;

  auto ends_with = [](const std::string &str, const std::string &suffix) {
    return str.size() >= suffix.size() &&
           str.compare(str.size() - suffix.size(), suffix.size(), suffix) == 0;
  };
  bool force_ffmpeg =
      ends_with(lower_path, ".m4a") || ends_with(lower_path, ".aac") ||
      ends_with(lower_path, ".alac");

  if (force_ffmpeg) {
    if (!decode_with_ffmpeg(fs::path(wpath), mono_samples, sampleRate, channels))
      return 1;
  } else if (ends_with(lower_path, ".mp3")) {
    drmp3 mp3;
    if (!drmp3_init_file_w(&mp3, wpath.c_str(), NULL)) {
      std::cerr << "dr_mp3: Failed to open file\n";
      return 1;
    }

    sampleRate = mp3.sampleRate;
    channels = mp3.channels;

    drmp3_uint64 maxFrames = (drmp3_uint64)sampleRate * 60;
    drmp3_uint64 totalFrames = drmp3_get_pcm_frame_count(&mp3);
    if (totalFrames == 0)
      totalFrames = maxFrames;

    drmp3_uint64 framesToRead = std::min(totalFrames, maxFrames);
    std::vector<float> samples(framesToRead * channels);

    drmp3_uint64 framesRead =
        drmp3_read_pcm_frames_f32(&mp3, framesToRead, samples.data());

    mono_samples.resize(framesRead);
    for (size_t i = 0; i < framesRead; i++) {
      float mix = 0.0f;
      for (int c = 0; c < channels; c++)
        mix += samples[i * channels + c];
      mono_samples[i] = mix / (float)channels;
    }

    drmp3_uninit(&mp3);
  } else if (ends_with(lower_path, ".flac")) {
    drflac *pFlac = drflac_open_file_w(wpath.c_str(), NULL);
    if (!pFlac) {
      std::cerr << "dr_flac: Failed to open file\n";
      return 1;
    }

    sampleRate = pFlac->sampleRate;
    channels = pFlac->channels;

    drflac_uint64 maxFrames = (drflac_uint64)sampleRate * 60;
    drflac_uint64 framesToRead = std::min(pFlac->totalPCMFrameCount, maxFrames);

    std::vector<float> samples(framesToRead * channels);
    drflac_uint64 framesRead =
        drflac_read_pcm_frames_f32(pFlac, framesToRead, samples.data());

    mono_samples.resize(framesRead);
    for (size_t i = 0; i < framesRead; i++) {
      float mix = 0.0f;
      for (int c = 0; c < channels; c++)
        mix += samples[i * channels + c];
      mono_samples[i] = mix / (float)channels;
    }

    drflac_close(pFlac);
  } else if (ends_with(lower_path, ".ogg")) {
    int error;
    stb_vorbis *pVorbis = nullptr;

#ifdef _WIN32
    FILE *f = _wfopen(wpath.c_str(), L"rb");
    if (f)
      pVorbis = stb_vorbis_open_file(f, 1, &error, NULL);
#else
    pVorbis = stb_vorbis_open_filename(
        std::string(wfilepath.begin(), wfilepath.end()).c_str(), &error, NULL);
#endif

    if (!pVorbis) {
      std::cerr << "stb_vorbis: Failed to open file\n";
      return 1;
    }

    stb_vorbis_info info = stb_vorbis_get_info(pVorbis);
    sampleRate = info.sample_rate;
    channels = info.channels;

    int maxFrames = sampleRate * 60;
    std::vector<float> samples(maxFrames * channels);

    int framesRead = stb_vorbis_get_samples_float_interleaved(
        pVorbis, channels, samples.data(), maxFrames * channels);

    mono_samples.resize(framesRead);
    for (int i = 0; i < framesRead; i++) {
      float mix = 0.0f;
      for (int c = 0; c < channels; c++)
        mix += samples[i * channels + c];
      mono_samples[i] = mix / (float)channels;
    }

    stb_vorbis_close(pVorbis);
  } else {
    drwav wav;
    if (!drwav_init_file_w(&wav, wpath.c_str(), NULL)) {
      if (!decode_with_ffmpeg(fs::path(wpath), mono_samples, sampleRate, channels)) {
        std::cerr << "dr_wav: Failed to open file\n";
        return 1;
      }
    } else {
      sampleRate = wav.sampleRate;
      channels = wav.channels;

      drwav_uint64 maxFrames = (drwav_uint64)sampleRate * 60;
      drwav_uint64 totalFrames = wav.totalPCMFrameCount;

      if (totalFrames == 0 && fs::file_size(wpath) > 100) {
        drwav_uint64 byteCount = fs::file_size(wpath) - 44;
        int bytesPerSample =
            (wav.bitsPerSample > 0) ? (wav.bitsPerSample / 8) : 2;

        if (bytesPerSample == 0 || wav.channels == 0) {
          std::cerr << "Invalid WAV format\n";
          return 1;
        }

        totalFrames = byteCount / (wav.channels * bytesPerSample);
      }

      drwav_uint64 framesToRead = std::min(totalFrames, maxFrames);

      std::vector<float> samples(framesToRead * channels);
      drwav_uint64 framesRead =
          drwav_read_pcm_frames_f32(&wav, framesToRead, samples.data());

      mono_samples.resize(framesRead);
      for (size_t i = 0; i < framesRead; i++) {
        float mix = 0.0f;
        for (int c = 0; c < channels; c++)
          mix += samples[i * channels + c];
        mono_samples[i] = mix / (float)channels;
      }

      drwav_uninit(&wav);
    }
  }

  int health = health_check(mono_samples);
  if (health != 0)
    return health;

  Audio_features features = compute_features(mono_samples, sampleRate);

  std::cout << "{\"vector\":[" << features.brightness << ","
            << features.percussivity << "," << features.fft_register << ","
            << features.zcr << "," << features.decay << ",";

  for (int i = 0; i < 11; i++)
    std::cout << features.chroma[i] << ",";

  std::cout << features.chroma[11] << "," << features.active_duration << ","
            << features.loopiness_score << "," << features.transient_tail_score << "],"
            << "\"feature_space_version\":\"unshuffle-audio-v1\","
            << "\"extractor_version\":\"unshuffle_extractor 1.0.0\","
            << "\"analysis_status\":\"ok\","
            << "\"feature_schema\":[";
  for (int i = 0; i < 20; i++) {
    if (i > 0)
      std::cout << ",";
    std::cout << "\"" << FEATURE_SCHEMA[i] << "\"";
  }
  std::cout << "],\"features\":{"
            << "\"brightness\":" << features.brightness << ","
            << "\"percussivity\":" << features.percussivity << ","
            << "\"fft_register\":" << features.fft_register << ","
            << "\"zcr\":" << features.zcr << ","
            << "\"decay\":" << features.decay << ",";
  for (int i = 0; i < 12; i++) {
    std::cout << "\"chroma_" << i << "\":" << features.chroma[i] << ",";
  }
  std::cout << "\"active_duration\":" << features.active_duration << ","
            << "\"loopiness_score\":" << features.loopiness_score
            << ",\"transient_tail_score\":" << features.transient_tail_score
            << "}}" << std::endl;

  return 0;
}
Audio_features compute_features(std::vector<float> &samples, int sampleRate) {
  const int fftSize = 2048;
  const int hopSize = fftSize / 2;
  if (samples.size() < fftSize) {
    samples.resize(fftSize, 0.0f);
  }
  const size_t size = samples.size();

  // Peak normalization keeps scalar features comparable across input gain.
  float max_val = 0.0f;
  for (float s : samples)
    max_val = std::max(max_val, std::abs(s));

  if (max_val > 0.0f) {
    for (float &s : samples)
      s /= max_val;
  }

  // Silence-invariant duration: measure the span between the first and last
  // frames with meaningful energy, ignoring padded leading/trailing silence.
  float active_duration = (float)size / (float)sampleRate;
  const size_t activity_window = std::min<size_t>(1024, size);
  const size_t activity_hop = std::max<size_t>(1, activity_window / 2);
  const float activity_threshold = 1e-4f; // RMS on peak-normalized audio.
  size_t active_start = size;
  size_t active_end = 0;

  for (size_t start = 0; start + activity_window <= size; start += activity_hop) {
    float sum_sq = 0.0f;
    for (size_t i = 0; i < activity_window; i++) {
      float curr = samples[start + i];
      sum_sq += curr * curr;
    }
    float rms = std::sqrt(sum_sq / (float)activity_window);
    if (rms >= activity_threshold) {
      active_start = std::min(active_start, start);
      active_end = std::max(active_end, start + activity_window);
    }
  }

  if (active_start < active_end) {
    active_duration = (float)(active_end - active_start) / (float)sampleRate;
  }

  float zcr_count = 0.0f;
  std::vector<float> chroma(12, 0.0f);

  float peak = 0.0f;
  size_t peak_i = 0;

  for (size_t i = 0; i < size; i++) {
    float s = samples[i];
    if (i > 0 && ((s > 0) != (samples[i - 1] > 0)))
      zcr_count++;

    if (std::abs(s) > peak) {
      peak = std::abs(s);
      peak_i = i;
    }
  }

  // Windowed decay estimates the post-peak energy tail.
  size_t step = std::max((size_t)1, (size_t)sampleRate / 200);
  size_t max_decay_samples = std::min(size, peak_i + (size_t)sampleRate * 2);

  float decay_sum = 0.0f;
  int decay_count = 0;
  float decay_floor = std::max(1e-4f, peak * 0.001f);

  for (size_t i = peak_i + 1; i < max_decay_samples; i += step) {
    float val = std::abs(samples[i]);
    if (val >= decay_floor) {
      decay_sum += std::log(val + 1e-6f);
      decay_count++;
    }
  }

  float decay = (decay_count > 0) ? decay_sum / decay_count : -10.0f;

  CVector x(fftSize, Complex(0.0));

  float total_mag_weight = 0.0f;
  float centroid_accum = 0.0f;
  float peak_bin_weight_accum = 0.0f;

  float prev_energy = 1e-6f; // Small epsilon to allow first-frame transients
  float percussivity_count = 0.0f;
  float energy_sum_for_weighting = 0.0f;
  float zcr_weighted_accum = 0.0f;
  float zcr_energy_sum = 0.0f;

  for (size_t start = 0; start + fftSize <= size; start += hopSize) {
    for (int i = 0; i < fftSize; i++) {
      float w = 0.5f * (1.0f - std::cos(2.0f * PI * i / (fftSize - 1)));
      x[i] = Complex(samples[start + i] * w, 0.0);
    }

    fft(x);

    float frame_energy = 0.0f;
    float frame_sample_energy = 0.0f;
    float frame_zcr = 0.0f;
    float max_mag = 0.0f;
    int peak_bin = 0;

    for (int i = 0; i < fftSize; i++) {
      float curr = samples[start + i];
      frame_sample_energy += curr * curr;
      if (i > 0 && ((curr > 0) != (samples[start + i - 1] > 0)))
        frame_zcr += 1.0f;
    }

    for (int i = 0; i < fftSize / 2; i++) {
      float mag = (float)std::abs(x[i]) + 1e-12f;
      float freq = (float)i * sampleRate / fftSize;

      frame_energy += mag;

      if (mag > max_mag) {
        max_mag = mag;
        peak_bin = i;
      }

      // Energy-weighted Centroid
      centroid_accum += mag * freq;
      total_mag_weight += mag;

      // Chroma (Energy-weighted)
      if (freq >= 80.0f) {
        float note = 12.0f * std::log2(freq / 440.0f) + 69.0f;
        int idx = ((int)std::round(note) % 12 + 12) % 12;
        chroma[idx] += mag * mag;
      }
    }

    // Energy weighting prevents silent frames from diluting transient and register features.
    if (prev_energy > 0.0f) {
      float ratio = frame_energy / prev_energy;
      if (ratio > 1.8f) // Threshold for transient
        percussivity_count += frame_energy;
    }

    peak_bin_weight_accum += (float)peak_bin * frame_energy;
    zcr_weighted_accum += (frame_zcr / (float)(fftSize - 1)) * frame_sample_energy;
    zcr_energy_sum += frame_sample_energy;
    energy_sum_for_weighting += frame_energy;
    prev_energy = frame_energy;
  }

  // Normalize by total energy rather than frame count so silence does not dominate.
  float percussivity = (energy_sum_for_weighting > 0.0f)
                           ? (percussivity_count / energy_sum_for_weighting)
                           : 0.0f;
  float brightness =
      (total_mag_weight > 0.0f) ? (centroid_accum / total_mag_weight) : 0.0f;

  float fft_register = 0.0f;
  if (energy_sum_for_weighting > 0.0f) {
    float avg_bin = peak_bin_weight_accum / energy_sum_for_weighting;
    float freq = avg_bin * sampleRate / fftSize;
    fft_register = (freq > 0.0f) ? std::log2(freq) : 0.0f;
  }

  // Normalize chroma
  float max_chroma = *std::max_element(chroma.begin(), chroma.end());
  if (max_chroma > 0.0f)
    for (auto &c : chroma)
      c /= max_chroma;

  float final_zcr = (zcr_energy_sum > 0.0f)
                        ? (zcr_weighted_accum / zcr_energy_sum)
                        : ((size > 1) ? (zcr_count / (float)(size - 1)) : 0.0f);

  // Final Scaling for Perceptual Space
  brightness /= 10000.0f;          // Hz normalization
  fft_register /= 16.0f;           // log2(freq) normalization
  decay = (decay + 10.0f) / 10.0f; // Shift log range

  const size_t envelope_window = std::max<size_t>(1, (size_t)sampleRate / 20);
  std::vector<float> envelope;
  for (size_t start = 0; start < size; start += envelope_window) {
    size_t end = std::min(size, start + envelope_window);
    float sum_sq = 0.0f;
    for (size_t i = start; i < end; i++)
      sum_sq += samples[i] * samples[i];
    envelope.push_back(std::sqrt(sum_sq / (float)std::max<size_t>(1, end - start)));
  }

  float envelope_peak = envelope.empty() ? 0.0f : *std::max_element(envelope.begin(), envelope.end());
  float envelope_crossing_rate = 0.0f;
  float active_coverage = 0.0f;
  float tail_ratio = 0.0f;
  float decay_ratio = 0.0f;
  float peak_position = 0.0f;
  float loopiness_score = 0.0f;
  float transient_tail_score = 0.0f;

  if (envelope_peak > 0.0f && !envelope.empty()) {
    std::vector<float> norm;
    norm.reserve(envelope.size());
    for (float value : envelope)
      norm.push_back(value / envelope_peak);

    const float active_threshold = 0.03f;
    float active_sum = 0.0f;
    int active_count = 0;
    for (float value : norm) {
      if (value >= active_threshold) {
        active_sum += value;
        active_count++;
      }
    }
    active_coverage = (float)active_count / (float)norm.size();
    float active_mean = active_count > 0 ? active_sum / (float)active_count : 0.0f;

    int crossings = 0;
    int previous_side = -1;
    for (float value : norm) {
      if (value < active_threshold)
        continue;
      int side = value >= active_mean ? 1 : 0;
      if (previous_side != -1 && side != previous_side)
        crossings++;
      previous_side = side;
    }
    envelope_crossing_rate =
        std::min(1.0f, (float)crossings / std::max(1.0f, active_duration * 4.0f));

    size_t segment = std::max<size_t>(1, norm.size() / 5);
    float head_sum = 0.0f;
    float tail_sum = 0.0f;
    for (size_t i = 0; i < segment; i++) {
      head_sum += norm[i];
      tail_sum += norm[norm.size() - segment + i];
    }
    float head_mean = head_sum / (float)segment;
    float tail_mean = tail_sum / (float)segment;
    tail_ratio = tail_mean;
    decay_ratio = tail_mean / std::max(0.001f, head_mean);
    peak_position =
        (float)(std::max_element(norm.begin(), norm.end()) - norm.begin()) /
        (float)std::max<size_t>(1, norm.size() - 1);

    loopiness_score = std::clamp(
        (active_coverage * 0.45f) + (tail_ratio * 0.30f) +
            (envelope_crossing_rate * 0.25f),
        0.0f, 1.0f);
    transient_tail_score = 0.0f;
    if (active_duration >= 1.0f) {
      float early_peak = std::clamp((0.45f - peak_position) / 0.45f, 0.0f, 1.0f);
      float low_tail = std::clamp((0.25f - tail_ratio) / 0.25f, 0.0f, 1.0f);
      float decay_drop = std::clamp((0.45f - decay_ratio) / 0.45f, 0.0f, 1.0f);
      float low_crossing = 1.0f - envelope_crossing_rate;
      transient_tail_score = std::clamp(
          (early_peak * 0.30f) + (low_tail * 0.30f) +
              (decay_drop * 0.25f) + (low_crossing * 0.15f),
          0.0f, 1.0f);
    }
  }

  return {brightness, percussivity, fft_register, final_zcr, decay,
          active_duration, loopiness_score, transient_tail_score, chroma};
}
