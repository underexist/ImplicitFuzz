cmake_minimum_required(VERSION 3.23)

project(
  implicitfuzz-extract
  VERSION 0.1.0
  DESCRIPTION "SVF-based static extraction driver for ImplicitFuzz"
  LANGUAGES C CXX)

if(DEFINED LLVM_DIR)
  set(ENV{LLVM_DIR} "${LLVM_DIR}")
elseif(DEFINED ENV{LLVM_DIR})
  set(LLVM_DIR $ENV{LLVM_DIR})
else()
  message(FATAL_ERROR "LLVM_DIR is not set. Export LLVM_DIR to your LLVM 21 install prefix.")
endif()

if(DEFINED ENV{LLVM_DIR})
  set(CMAKE_CXX_STANDARD 17)
  if(CMAKE_BUILD_TYPE MATCHES "Debug")
    set(CMAKE_CXX_FLAGS "-fPIC -std=gnu++17 -O0 -fno-rtti -Wno-deprecated")
  else()
    set(CMAKE_CXX_FLAGS "-fPIC -std=gnu++17 -O3 -fno-rtti -Wno-deprecated")
  endif()
  set(CMAKE_C_FLAGS "-fPIC")
endif()

find_package(LLVM REQUIRED CONFIG)
message(STATUS "LLVM ${LLVM_VERSION} @ ${LLVM_DIR}")

list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)

add_definitions(${LLVM_DEFINITIONS})
include_directories(SYSTEM ${LLVM_INCLUDE_DIRS})
link_directories(${LLVM_LIBRARY_DIRS})

if(LLVM_LINK_LLVM_DYLIB)
  set(llvm_libs LLVM)
else()
  llvm_map_components_to_libnames(
    llvm_libs bitwriter core ipo irreader instcombine instrumentation target
    linker analysis scalaropts support)
endif()

if(NOT LLVM_ENABLE_RTTI)
  add_compile_options("-fno-rtti")
endif()
if(NOT LLVM_ENABLE_EH)
  add_compile_options("-fno-exceptions")
endif()

if(NOT DEFINED SVF_DIR AND DEFINED ENV{SVF_DIR})
  set(SVF_DIR $ENV{SVF_DIR})
endif()
find_package(SVF REQUIRED CONFIG HINTS ${SVF_DIR} ${SVF_DIR}/Release-build
                                      ${SVF_DIR}/Debug-build)

message(STATUS "SVF install root: ${SVF_INSTALL_ROOT}")

include_directories(SYSTEM ${SVF_INSTALL_INCLUDE_DIR})
link_directories(${SVF_INSTALL_LIB_DIR})

set(SVF_LIB SvfLLVM SvfCore)

find_library(Z3_LIBRARIES REQUIRED NAMES z3 HINTS ${Z3_DIR} ENV Z3_DIR
             PATH_SUFFIXES bin lib)
find_path(Z3_INCLUDES REQUIRED NAMES z3++.h HINTS ${Z3_DIR} ENV Z3_DIR
          PATH_SUFFIXES include z3)
message(STATUS "Z3: ${Z3_LIBRARIES}")

link_libraries(${Z3_LIBRARIES})
include_directories(SYSTEM ${Z3_INCLUDES})

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

add_executable(implicitfuzz-extract src/implicitfuzz-extract.cpp)
target_link_libraries(implicitfuzz-extract PRIVATE ${llvm_libs} ${SVF_LIB})

install(TARGETS implicitfuzz-extract RUNTIME DESTINATION bin)
