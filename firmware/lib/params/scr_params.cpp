#include "scr_params.h"

#include <cmath>
#include <cstring>

#include "scr_protocol.h"

namespace scr {

namespace {
bool sameBits(float a, float b) { return std::memcmp(&a, &b, sizeof(float)) == 0; }
}  // namespace

ParamStore::ParamStore() {
  for (int i = 0; i < params::kCount; ++i) {
    value_[i] = params::kTable[i].def;
    saved_[i] = params::kTable[i].def;
    source_[i] = proto::kParamSourceDefault;
  }
  rehash();
}

int ParamStore::indexOf(uint16_t id) {
  for (int i = 0; i < params::kCount; ++i) {
    if (params::kTable[i].id == id) return i;
  }
  return -1;
}

uint8_t ParamStore::check(uint16_t id, float value) {
  const int i = indexOf(id);
  if (i < 0) return proto::kAckUnknownParam;
  const params::ParamDef& d = params::kTable[i];
  if (!std::isfinite(value)) return proto::kAckOutOfRange;
  if (value < d.min || value > d.max) return proto::kAckOutOfRange;
  if (d.type != params::kFloat && std::floor(value) != value) return proto::kAckOutOfRange;
  return proto::kAckOk;
}

float ParamStore::get(uint16_t id) const {
  const int i = indexOf(id);
  return i < 0 ? 0.0f : value_[i];
}

uint8_t ParamStore::set(uint16_t id, float value, bool* changed) {
  if (changed) *changed = false;
  const uint8_t st = check(id, value);
  if (st != proto::kAckOk) return st;
  const int i = indexOf(id);
  if (value == 0.0f) value = 0.0f;  // -0.0 -> +0.0: one encoding per value for the hash
  if (!sameBits(value_[i], value)) {
    value_[i] = value;
    if (changed) *changed = true;
  }
  source_[i] = proto::kParamSourceSet;
  rehash();
  return st;
}

bool ParamStore::seedDefault(uint16_t id, float value) {
  if (check(id, value) != proto::kAckOk) return false;
  const int i = indexOf(id);
  if (value == 0.0f) value = 0.0f;
  value_[i] = value;
  saved_[i] = value;
  source_[i] = proto::kParamSourceDefault;
  rehash();
  return true;
}

int ParamStore::load(hal::IParamStorage* storage) {
  int loaded = 0;
  rejected_on_load_ = 0;
  if (storage == nullptr) return 0;
  for (int i = 0; i < params::kCount; ++i) {
    float v = 0.0f;
    if (!storage->read(params::kTable[i].id, &v)) continue;  // NVS holds nothing for it
    if (check(params::kTable[i].id, v) != proto::kAckOk) {
      ++rejected_on_load_;  // out of the compiled bounds: the default stays
      continue;
    }
    if (v == 0.0f) v = 0.0f;
    value_[i] = v;
    saved_[i] = v;
    source_[i] = proto::kParamSourceNvs;
    ++loaded;
  }
  rehash();
  return loaded;
}

bool ParamStore::save(hal::IParamStorage* storage) {
  beginSave();
  for (;;) {
    const SaveStep st = saveStep(storage);
    if (st != SaveStep::InProgress) return st == SaveStep::Done;
  }
}

ParamStore::SaveStep ParamStore::saveStep(hal::IParamStorage* storage) {
  if (!saving_) return SaveStep::Failed;
  if (storage == nullptr) {
    saving_ = false;
    return SaveStep::Failed;
  }
  if (save_index_ < params::kCount) {
    if (!storage->write(params::kTable[save_index_].id, value_[save_index_])) {
      saving_ = false;
      return SaveStep::Failed;
    }
    ++save_index_;
    return SaveStep::InProgress;
  }
  saving_ = false;
  if (!storage->commit()) return SaveStep::Failed;
  for (int i = 0; i < params::kCount; ++i) {
    saved_[i] = value_[i];
    if (source_[i] == proto::kParamSourceSet) source_[i] = proto::kParamSourceNvs;
  }
  return SaveStep::Done;
}

bool ParamStore::unsaved() const {
  for (int i = 0; i < params::kCount; ++i) {
    if (!sameBits(value_[i], saved_[i])) return true;
  }
  return false;
}

bool ParamStore::anyFromNvs() const {
  for (int i = 0; i < params::kCount; ++i) {
    if (source_[i] == proto::kParamSourceNvs) return true;
  }
  return false;
}

void ParamStore::rehash() {
  uint32_t h = proto::kFnvOffset;
  for (int i = 0; i < params::kCount; ++i) h = proto::fnv1aParam(h, params::kTable[i].id, value_[i]);
  hash_ = h;
}

uint32_t defaultParamHash() {
  uint32_t h = proto::kFnvOffset;
  for (int i = 0; i < params::kCount; ++i) h = proto::fnv1aParam(h, params::kTable[i].id, params::kTable[i].def);
  return h;
}

}  // namespace scr
