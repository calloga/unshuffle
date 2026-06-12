cmake_minimum_required(VERSION 3.10)
project(unshuffle_extractor)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include_directories(.)

add_executable(unshuffle_extractor 
    unshuffle_extractor.cpp 
    stb_vorbis_impl.cpp
    fft_utils.h
)

if(UNIX AND NOT APPLE)
    target_link_libraries(unshuffle_extractor m)
endif()
