# D6 bounded actuation transaction

## Authority and scope

The user explicitly permits autonomous implementation and isolated experiments within D1–D6. No additional per-slice approval is requested. This slice connects an existing `ControlTick` to an injected movement port, with a mandatory neutral command and separately evaluated stopping telemetry. It does not launch a simulator, arm/take off, establish a free-space certificate, collect Town03 data, change trained models, or claim D6 fully accepted.

## Root cause and alternatives

`BoundedControlLoop` currently returns a <=50 ms command, but its observation/action/energy expiry is not carried to the eventual transport. A delayed consumer could send it after those checks expired. `NO_COMMAND` does not brake a real vehicle. The legacy `contract_probe` is not the solution: its bootstrap and diagnostic commands have different durations/speeds and lack the current geometry gates. The latest independent geometry run reports a grounded/static vehicle, not airborne readiness.

1. Send synchronously inside the selector loop: simple, but a stalled RPC blocks the caller and obscures expiry between compile and send.
2. **Chosen:** one bounded, asynchronous execute/cancel/neutral transaction, with expiry checked again immediately before movement RPC. No queue and no worker replacement after timeout. A separate stopping-telemetry verifier consumes fresh samples.
3. Reuse the legacy bootstrap: rejected because it bypasses the newer bounded-prefix safety contract. A ground-to-air lifecycle requires its own measured geometry and remains a separate dependency.

## Interfaces and responsibilities

- `ControlTick.dispatch_deadline_s`: optional absolute monotonic deadline, present only for a successfully compiled prefix. It is the minimum of original observation expiry, action lifetime and (if applicable) energy deadline. Missing deadlines fail closed at transport. Existing mock consumers remain compatible.
- `FlightTelemetry`: immutable local acquisition bracket, exact source nanosecond timestamp, landed enum, NED linear/angular velocity, collision flag and collision-event timestamp. It is own-vehicle telemetry, not target information or a replacement for the loop's measured clearance.
- `BoundedActuator`: caller-owned session, injected `transport_factory`, single persistent daemon worker, one occupied slot. Caller operations return promptly even if an external RPC hangs. It accepts only current-generation `COMMAND_PREFIX` ticks, finite bounded commands, fresh airborne telemetry and non-replayed requests. A worker rechecks cancellation, telemetry expiry and command lease after constructing the transport and just before sending. Transport lives on that worker thread.
- Each dispatched transaction attempts cancel and an explicit `(0,0,0)` / zero-yaw / 50 ms neutral command after the prefix, including an error/rejection. Cancel completion is not a stopping acknowledgement. Neutral acknowledgement is not physical stopping. No retries of uncertain movement commands.
- A 200 ms transaction watchdog latches `OWNER_TERMINATION_REQUIRED` on timeout; it cannot kill a thread or prove a remote command canceled. Late results cannot clear the latch, and no second movement worker is created. `close` revokes queued work, is nonblocking and does not claim cleanup complete. Only the existing owned-runtime supervisor may terminate its own process.
- `AirSimMovementPort`: thin injected-client adapter, world NED velocity, `MaxDegreeOfFreedom`, explicit rate-mode yaw, explicit vehicle name, `.get()` not `.join()` for RPC errors and strict boolean acknowledgement. It creates no client/process, arms nothing and has no default endpoint.
- `StopVerifier`: requires a successful neutral receipt plus three strictly advancing post-neutral own-state samples spanning >=100 ms, each linear speed <=0.05 m/s and angular speed <=0.05 rad/s, no reported/new collision, matching landed state, within a 500 ms observation window. Verdict is `TELEMETRY_SETTLED`, not certified safe hover, collision-free space or full physical braking validation.

## Safety and evidence boundaries

Keep original 0.3 m/s, 15 deg/s, 50 ms prefix, 200 ms observation, resource and 3 m clearance limits. A grounded drone cannot be promoted to airborne-ready by changing thresholds; a historical collision is not silently erased. The existing loop remains responsible for current causal belief, fresh measured clearance/collision and explicit energy checks. The actuator requires its issued lease; it cannot derive a safety lease from arbitrary raw point-cloud minima.

This first transport is a **transactional smoke path**, not a sustained 20 Hz live controller: explicit neutral commands and stop observation add latency. Report actual counts/time and this limitation. Do not integrate automatic arm/ascent or call this flight-ready. No live run is justified by the previous static geometry evidence; use deterministic injected ports for this slice and preserve the live blocker.

## Verification

TDD for lease propagation, grounded/unknown/stale/replay/generation rejection, worker-delay expiry, exact NED RPC arguments, strict acknowledgements, cancel/neutral on failures, watchdog latch, nonblocking close, and post-neutral stop evidence. A real event-blocked worker tests bounded caller latency; deterministic controlled execution tests expiry without relying on sleep. The offline smoke must exercise the production pipeline, loop, actuator and verifier, label all transport/telemetry synthetic, write a fresh output directory and never contact AirSim/Jev. Full regression, both Python versions' compile/smoke, independent review, report and feature-branch push follow.
