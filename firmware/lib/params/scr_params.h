// Runtime parameters (interface spec A2/A4/A9, docs/SERIAL_PROTOCOL.md section 10).
//
// The table (ids, names, types, defaults and the HARD min/max bounds) is compiled in from
// scr_param_table.h, which is GENERATED from shared/scr_serial/params.json. Nothing can be set
// outside [min, max]: every path into the store (PARAM_SET, NVS load) goes through check().
// Hardware independent; persistence goes through hal::IParamStorage (NVS on the ESP32, an
// in-memory stub off-target).
#pragma once

#include <cstdint>

#include "scr_hal.h"
#include "scr_param_table.h"

namespace scr {

class ParamStore {
 public:
  ParamStore();  // every parameter at its compiled default (source DEFAULT)

  static constexpr uint16_t count() { return params::kCount; }
  static int indexOf(uint16_t id);  // -1 = not a parameter
  static const params::ParamDef& def(int index) { return params::kTable[index]; }

  // proto::kAckOk, kAckUnknownParam, or kAckOutOfRange (NaN/inf, outside [min, max], or a
  // non-integer value for an int/bool parameter). Does not look at the robot's state: the
  // "no change while the tracks move" rule (BUSY) is the application's.
  static uint8_t check(uint16_t id, float value);

  float get(uint16_t id) const;  // 0 for an unknown id
  float valueAt(int index) const { return value_[index]; }
  uint8_t sourceAt(int index) const { return source_[index]; }

  // Applies a checked value. Returns the check() status; the store is unchanged unless kAckOk.
  // *changed (optional) = the stored value is now different (bitwise) from before.
  uint8_t set(uint16_t id, float value, bool* changed = nullptr);

  // Replaces the compiled default of one parameter (tests and AppConfig seeding only). The value
  // must pass check(); returns false (and changes nothing) otherwise.
  bool seedDefault(uint16_t id, float value);

  // Reads every parameter from storage. A stored value that fails check() (table bounds changed,
  // damaged flash) is ignored: the compiled default stays. Returns the number of values loaded.
  int load(hal::IParamStorage* storage);
  // Writes every parameter and commits. False when the storage failed (nothing is marked saved).
  bool save(hal::IParamStorage* storage);
  int rejectedOnLoad() const { return rejected_on_load_; }

  uint32_t hash() const { return hash_; }  // FNV-1a over (id u16, value f32) in id order
  bool unsaved() const;                    // some value differs from what NVS holds
  bool anyFromNvs() const;

 private:
  void rehash();

  float value_[params::kCount];
  float saved_[params::kCount];  // what NVS holds (the compiled default when NVS holds nothing)
  uint8_t source_[params::kCount];
  uint32_t hash_ = 0;
  int rejected_on_load_ = 0;
};

// FNV-1a of the compiled defaults of the table (what robotd computes for an empty calibration).
uint32_t defaultParamHash();

}  // namespace scr
