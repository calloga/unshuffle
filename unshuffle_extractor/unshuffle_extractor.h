#ifndef UNSHUFFLE_EXTRACTOR_H
#define UNSHUFFLE_EXTRACTOR_H
#include <vector>

struct Audio_features {
  float brightness;
  float percussivity;
  float fft_register;
  float zcr;
  float decay;
  float active_duration;
  float loopiness_score;
  float transient_tail_score;
  std::vector<float> chroma;
};

Audio_features compute_features(std::vector<float> &samples, int sampleRate);

#endif
