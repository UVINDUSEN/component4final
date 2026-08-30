// lib/services/fusion_risk_service.dart
//
// The missing half of Aura's integration with the R26-DS-012 Central Backend.
//
// WHAT WAS ALREADY THERE
// ----------------------
// ApiService could redeem a clinician's pairing code (POST /v1/subjects/pair).
// That is the clinician-first order: the doctor enrols the patient, reads out a
// code, the patient types it in.
//
// WHAT WAS MISSING
// ----------------
// The product runs patient-first. Aura mints a participant id at registration
// and the clinician meets it later by scanning a QR. In that order nothing ever
// created the backend's `app_user_id` alias, so:
//
//   * POST /v1/ingest/contextual     -> 404 no subject for that app_user_id
//   * POST /v1/ingest/physiological  -> 404 no subject for that app_user_id
//
// Both the psychological (GAD-7 + demographics -> C4/DCAR) and the digital
// phenotyping (-> C1) scores were therefore never reaching fusion from the
// patient side. Only the clinician's C3 note landed, the gate found one usable
// modality where it needs two, and every surface in both apps showed GREY.
//
// ApiService also carried a dead `sendToFusionModel` pointing at
// `https://PLACEHOLDER_FUSION_ENDPOINT.hf.space/fuse`. That route exists in no
// service in any repository. Delete it rather than leave it: a placeholder that
// silently returns `{'success': false}` looks like a network problem forever.
//
// WHAT THIS ADDS
// --------------
//   ensureEnrolled()      Aura claims its own subject_id at registration.
//   submitIntake()        GAD-7 + demographics -> C4.
//   submitPhysioWindow()  one 60s feature window -> C1.
//   latestRisk()          composite + band for the Aura home page.
//
// The home page shows composite and band ONLY. It deliberately does not show
// per-modality scores, weights, or anything derived from the clinician's note —
// the backend's patient view withholds them, and a patient reading "your
// clinical notes score is 0.83" with no clinician present is a harm.

import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'participant_identity_service.dart';

/// What the Aura home page renders. `band` is one of GREEN / AMBER / RED, or
/// GREY when the backend has not been able to produce a composite yet.
class FusionRisk {
  final double? composite;
  final String band;
  final String message;
  final DateTime? updatedAt;

  const FusionRisk({
    required this.composite,
    required this.band,
    required this.message,
    this.updatedAt,
  });

  /// GREY is the absence of an assessment, not a low one. It must never be
  /// drawn on the severity scale or averaged into anything — render it as an
  /// empty state.
  bool get isScored => composite != null && band != 'GREY';

  static const FusionRisk none = FusionRisk(
    composite: null,
    band: 'GREY',
    message: 'No assessment yet',
  );

  factory FusionRisk.fromJson(Map<String, dynamic> json) {
    final raw = json['composite'];
    return FusionRisk(
      composite: raw == null ? null : (raw as num).toDouble(),
      band: (json['band'] ?? 'GREY').toString(),
      message: (json['message'] ?? 'Assessment available').toString(),
      updatedAt: json['updated_at'] == null
          ? null
          : DateTime.tryParse(json['updated_at'].toString()),
    );
  }
}

class FusionRiskService {
  static const String _base = String.fromEnvironment(
    'BACKEND_BASE',
    defaultValue: 'https://finalize-humbly-monastery.ngrok-free.dev',
  );

  /// The backend checks one shared app token (main.py::_auth) on every route
  /// except /v1/subjects/pair. Shipping it in the binary is a known prototype
  /// limitation, documented rather than concealed: it is the same token the
  /// clinician app carries, and it authenticates the APP, not the person.
  static const String _token = String.fromEnvironment(
    'BACKEND_TOKEN',
    defaultValue: '',
  );

  static const Duration _timeout = Duration(seconds: 20);

  static String get _root => _base.trim().replaceFirst(RegExp(r'/$'), '');

  static Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token.isNotEmpty) 'Authorization': 'Bearer $_token',
      };

  // ── Enrolment ──────────────────────────────────────────────────────────────

  /// Claims a subject for this installation and caches the returned subject_id.
  ///
  /// Idempotent server-side, so a reinstall or a retry after a dropped response
  /// returns the same subject rather than forking the patient into two records.
  /// Call this once at the end of registration, before any ingest.
  static Future<String?> ensureEnrolled() async {
    final cached = await ParticipantIdentityService.getCentralSubjectId();
    if (cached != null && cached.isNotEmpty) return cached;

    final participantId = await _participantId();
    if (participantId == null) return null;

    try {
      final res = await http
          .post(
            Uri.parse('$_root/v1/subjects/self'),
            headers: _headers,
            body: jsonEncode({'app_user_id': participantId}),
          )
          .timeout(_timeout);

      if (res.statusCode != 200) return null;
      final subjectId = (jsonDecode(res.body)['subject_id'] ?? '').toString();
      if (subjectId.isEmpty) return null;

      await ParticipantIdentityService.saveCentralSubjectId(subjectId);
      return subjectId;
    } catch (_) {
      // Enrolment is retried on the next ingest. Failing here must never block
      // registration — the patient can still use Aura offline.
      return null;
    }
  }

  /// The pseudonymous participant id Aura minted at registration. This is the
  /// value the QR encodes and the value the clinician scans, so it is also the
  /// `app_user_id` the backend must key this subject by.
  static Future<String?> _participantId() async {
    final prefs = await SharedPreferences.getInstance();
    final id = prefs.getString(ParticipantIdentityService.participantIdKey);
    if (id == null || !ParticipantIdentityService.isParticipantId(id)) {
      return null;
    }
    return id;
  }

  // ── Ingest: psychological (C4 / DCAR) ─────────────────────────────────────

  /// Sends the GAD-7 items and demographics. The backend recomputes the GAD-7
  /// total server-side; a client-sent total is display only.
  ///
  /// Triggers fusion immediately on the backend — intake is a rare event.
  static Future<bool> submitIntake({
    required List<int> gad7Items,
    String? gender,
    double? age,
    String? edu,
    String? smoke,
    String? drink,
  }) async {
    if (gad7Items.length != 7 || gad7Items.any((v) => v < 0 || v > 3)) {
      throw ArgumentError('gad7Items must be exactly 7 values, each 0-3.');
    }
    final subjectId = await ensureEnrolled();
    if (subjectId == null) return false;

    return _post('/v1/ingest/contextual', {
      'subject_id': subjectId,
      'gad7_items': gad7Items,
      if (gender != null) 'gender': gender,
      if (age != null) 'age': age,
      if (edu != null) 'edu': edu,
      if (smoke != null) 'smoke': smoke,
      if (drink != null) 'drink': drink,
    });
  }

  // ── Ingest: digital phenotyping (C1) ──────────────────────────────────────

  /// One 60-second feature window, using the ten features C1 was trained on.
  ///
  /// Backend-side this is DEBOUNCED: fusion re-runs at most every
  /// AUTO_FUSION_DEBOUNCE_MIN minutes, so calling this every minute does not
  /// write 1,440 fusion rows per patient per day.
  static Future<bool> submitPhysioWindow({
    required DateTime windowStart,
    required DateTime windowEnd,
    required Map<String, double> features,
    int samplingHz = 1,
  }) async {
    final subjectId = await ensureEnrolled();
    if (subjectId == null) return false;

    return _post('/v1/ingest/physiological', {
      'subject_id': subjectId,
      'window_start': windowStart.toUtc().toIso8601String(),
      'window_end': windowEnd.toUtc().toIso8601String(),
      'sampling_hz': samplingHz,
      'features': features,
    });
  }

  static Future<bool> _post(String path, Map<String, dynamic> body) async {
    try {
      final res = await http
          .post(Uri.parse('$_root$path'),
              headers: _headers, body: jsonEncode(body))
          .timeout(_timeout);
      return res.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ── Egress: the Aura home page ────────────────────────────────────────────

  /// Reads the authoritative composite. Returns [FusionRisk.none] rather than
  /// throwing, so the home page degrades to an empty state instead of an error
  /// dialog — but it never substitutes a locally computed number.
  static Future<FusionRisk> latestRisk() async {
    final subjectId = await ParticipantIdentityService.getCentralSubjectId();
    if (subjectId == null || subjectId.isEmpty) return FusionRisk.none;

    try {
      final res = await http
          .get(Uri.parse('$_root/v1/patients/$subjectId/risk'),
              headers: _headers)
          .timeout(_timeout);
      if (res.statusCode != 200) return FusionRisk.none;
      final json = jsonDecode(res.body);
      if (json is! Map) return FusionRisk.none;
      return FusionRisk.fromJson(Map<String, dynamic>.from(json));
    } catch (_) {
      return FusionRisk.none;
    }
  }
}
