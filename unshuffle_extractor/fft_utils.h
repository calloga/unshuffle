#ifndef FFT_UTILS_H
#define FFT_UTILS_H

#include <vector>
#include <complex>
#include <cmath>
#include <algorithm>

typedef std::complex<double> Complex;
typedef std::vector<Complex> CVector;

const double PI = 3.14159265358979323846;

// Bit-reversal permutation
void bit_reverse(CVector& x) {
    size_t n = x.size();
    for (size_t i = 1, j = 0; i < n; i++) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1)
            j ^= bit;
        j ^= bit;
        if (i < j)
            std::swap(x[i], x[j]);
    }
}

// Static cache for twiddle factors
static std::vector<std::vector<Complex>> twiddle_cache;
static size_t cached_max_n = 0;

void precompute_twiddles(size_t max_n) {
    if (max_n <= cached_max_n) return;
    twiddle_cache.resize(std::log2(max_n) + 1);
    size_t k = 0;
    for (size_t len = 2; len <= max_n; len <<= 1, k++) {
        twiddle_cache[k].resize(len / 2);
        double ang = -2 * PI / len;
        for (size_t j = 0; j < len / 2; j++) {
            twiddle_cache[k][j] = Complex(std::cos(ang * j), std::sin(ang * j));
        }
    }
    cached_max_n = max_n;
}

// Iterative Cooley-Tukey FFT
void fft(CVector& x) {
    size_t n = x.size();
    bit_reverse(x);
    precompute_twiddles(n);

    size_t k = 0;
    for (size_t len = 2; len <= n; len <<= 1, k++) {
        for (size_t i = 0; i < n; i += len) {
            for (size_t j = 0; j < len / 2; j++) {
                Complex u = x[i + j];
                Complex v = x[i + j + len / 2] * twiddle_cache[k][j];
                x[i + j] = u + v;
                x[i + j + len / 2] = u - v;
            }
        }
    }
}

#endif
