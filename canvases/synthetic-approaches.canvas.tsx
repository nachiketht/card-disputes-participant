import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  PieChart,
  Pill,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const SOURCE =
  "Local 100-case synthetic bake-off, 2026-10-28. A1 = a1-now (rules). A2 = a2-now (rules then gpt-6-luna). A3 = a3-now (Jev first; Clef only if Jev is missing or fails; then gpt-6-luna on disagreement). Classifier v3 pinned. Cost from classification-audit usage: gpt-6-luna $0.10 / $0.50 per million in/out; Jev $0.042 / $0.00. Clef did not run. Latency is case-span p50/p95 from metrics.json. Run files under reports/synthetic and submissions/synthetic are generated and are not committed.";

const CHECKS = [
  "classification",
  "routing",
  "missing_information",
  "deadline",
  "required_evidence",
  "targeted_questions",
  "citations_valid",
  "facts",
  "policy_selection",
  "narrative_present",
  "no_restricted_values",
];

const APPROACHES = [
  {
    id: "1",
    name: "Rules only",
    subtitle: "No model. Keyword phrases plus duplicate pair check.",
    fullHits: 59,
    checks: [59, 69, 81, 67, 99, 81, 100, 100, 100, 100, 100],
    usdTotal: 0,
    modelCalls: 0,
    jevCalls: 0,
    wallS: 2.064,
    p50: 10.8,
    p95: 67.4,
    pMax: 164.2,
    slo: 1.0,
    routes: { manual_review: 74, ready_for_review: 24, request_information: 2 },
    paths: [
      ["rules_only", 58, 99],
      ["duplicate_unmatched", 1, 1],
    ],
  },
  {
    id: "2",
    name: "Rules then gpt-6-luna",
    subtitle: "Model only on eligible cases.",
    fullHits: 97,
    checks: [97, 98, 100, 98, 99, 100, 100, 100, 100, 100, 100],
    usdTotal: 0.004632,
    modelCalls: 55,
    jevCalls: 0,
    wallS: 75.561,
    p50: 1090.8,
    p95: 1742.7,
    pMax: 2561.0,
    slo: 0.98,
    routes: { manual_review: 45, ready_for_review: 34, request_information: 21 },
    paths: [
      ["haiku_assigned", 39, 39],
      ["rules_only", 41, 44],
      ["haiku_unclear", 9, 9],
      ["authorization_conflict", 7, 7],
      ["duplicate_unmatched", 1, 1],
    ],
  },
  {
    id: "3",
    name: "Jev then model",
    subtitle: "Clef is a fallback if Jev is missing or fails. Unused on this run.",
    fullHits: 96,
    checks: [96, 97, 100, 97, 99, 100, 100, 100, 100, 100, 100],
    usdTotal: 0.005363,
    modelCalls: 44,
    jevCalls: 55,
    wallS: 64.018,
    p50: 114.5,
    p95: 1632.8,
    pMax: 3346.7,
    slo: 0.99,
    routes: { manual_review: 46, ready_for_review: 33, request_information: 21 },
    paths: [
      ["haiku_assigned", 37, 37],
      ["rules_only", 41, 44],
      ["jev_ambiguous", 9, 10],
      ["authorization_conflict", 7, 7],
      ["jev_agreed", 1, 1],
      ["duplicate_unmatched", 1, 1],
    ],
  },
];

function usd(n: number) {
  if (n === 0) return "$0";
  return `$${n.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
}

export default function SyntheticApproachesCanvas() {
  return (
    <Stack gap={28}>
      <Stack gap={8}>
        <H1>Synthetic bake-off — class, cost, latency</H1>
        <Text tone="secondary">
          100 gold cases (CARD-S001–S100). Official score is full eval (every
          check must pass). Classification is the bake-off target. Drafter was
          off.
        </Text>
        <Text tone="tertiary" size="small">
          {SOURCE}
        </Text>
      </Stack>

      <Grid columns={5} gap={12}>
        <Stat value="59 / 100" label="A1 full eval" />
        <Stat value="97 / 100" label="A2 full eval" tone="success" />
        <Stat value="96 / 100" label="A3 full eval" tone="success" />
        <Stat value="$0.0046" label="A2 list-price USD (100 cases)" />
        <Stat value="$0.0054" label="A3 list-price USD (100 cases)" />
      </Grid>

      <Callout tone="success">
        A2 is the accuracy winner at 97/100 full eval, $0.0046 list
        for 100 cases. A3 is 96/100. Jev is priced: 55 ranker calls add
        $0.0017, so A3 totals $0.0054. Both beat the 2000 ms case SLO (A2
        0.98, A3 0.99, objective 0.95).
      </Callout>

      <Grid columns={3} gap={16}>
        {APPROACHES.map((a) => (
          <Card key={a.id}>
            <CardHeader trailing={<Pill size="sm">Approach {a.id}</Pill>}>
              {a.name}
            </CardHeader>
            <CardBody>
              <Stack gap={12}>
                <Text tone="secondary" size="small">
                  {a.subtitle}
                  {a.id === "2" ? (
                    <>
                      <br />
                      {"\u00a0"}
                    </>
                  ) : null}
                </Text>
                <Stat value={`${a.fullHits}/100`} label="Full eval" />
                <Stat value={usd(a.usdTotal)} label="List-price USD" />
                <Stat
                  value={`${Math.round(a.p95)} ms`}
                  label="Case-span p95"
                />
                <Text size="small">
                  Model calls {a.modelCalls}
                  {a.jevCalls ? ` · Jev ${a.jevCalls}` : ""} · wall {a.wallS}s ·
                  SLO {Math.round(a.slo * 100)}%
                </Text>
                <Text size="small" tone="secondary">
                  Routes: MR {a.routes.manual_review} · ready{" "}
                  {a.routes.ready_for_review} · RI{" "}
                  {a.routes.request_information}
                </Text>
              </Stack>
            </CardBody>
          </Card>
        ))}
      </Grid>

      <Divider />

      <Stack gap={12}>
        <H2>Cost and tokens</H2>
        <Text>
          List price from provider usage on this run. A1 is $0. Jev output is
          uncharged.
        </Text>
        <Grid columns={2} gap={16}>
          <Stack gap={8}>
            <H3>List-price USD per 1,000 cases</H3>
            <BarChart
              categories={["A1 rules", "A2 model", "A3 Jev+model"]}
              series={[
                {
                  name: "gpt-6-luna",
                  data: [0, 0.04632, 0.03639],
                  tone: "info",
                },
                {
                  name: "Jev",
                  data: [0, 0, 0.01724],
                  tone: "warning",
                },
              ]}
              stacked
              valuePrefix="$"
              height={220}
            />
            <Text tone="tertiary" size="small">
              Y-axis: USD per 1,000 cases. Source: sum of priced
              haiku.cost.usd and jev.cost.usd on the local a1-now / a2-now /
              a3-now audits. Those directories are gitignored under
              reports/synthetic.
            </Text>
          </Stack>
          <Stack gap={8}>
            <H3>Mean input tokens per call</H3>
            <BarChart
              categories={["A2 gpt-6-luna", "A3 gpt-6-luna", "A3 Jev"]}
              series={[
                {
                  name: "Mean tokens_in",
                  data: [460.5, 461.4, 749.7],
                  tone: "warning",
                },
              ]}
              height={220}
            />
            <Text tone="tertiary" size="small">
              Y-axis: mean input tokens from provider usage. 55 gpt-6-luna
              calls on A2, 44 on A3, 55 Jev calls on A3.
            </Text>
          </Stack>
        </Grid>
        <Table
          headers={[
            "Approach",
            "Model calls",
            "Jev calls",
            "gpt-6-luna USD",
            "Jev USD",
            "USD total",
            "USD / case",
          ]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right"]}
          rows={[
            ["A1 rules", "0", "0", "$0", "$0", "$0", "$0"],
            [
              "A2",
              "55",
              "0",
              "$0.004632",
              "$0",
              "$0.004632",
              "$0.000046",
            ],
            [
              "A3",
              "44",
              "55",
              "$0.003639",
              "$0.001724",
              "$0.005363",
              "$0.000054",
            ],
          ]}
        />
        <Text tone="tertiary" size="small">
          A2 gpt-6-luna: 25,328 in / 4,196 out. A3 gpt-6-luna: 20,301 in /
          3,212 out. A3 Jev: 41,231 in / 4,081 out (output uncharged).
        </Text>
      </Stack>

      <Divider />

      <Stack gap={12}>
        <H2>Latency</H2>
        <Text>
          Case-span is wall time per case. A3 median is low because
          Jev-ambiguous and rules-only cases skip gpt-6-luna. The p95 is still
          the model tail. SLO: case drafted within 2000 ms, objective 0.95 (5
          bad cases allowed).
        </Text>
        <Grid columns={2} gap={16}>
          <Stack gap={8}>
            <H3>Case-span latency (ms)</H3>
            <BarChart
              categories={["A1", "A2", "A3"]}
              series={[
                { name: "p50", data: [10.8, 1090.8, 114.5], tone: "info" },
                { name: "p95", data: [67.4, 1742.7, 1632.8], tone: "warning" },
              ]}
              valueSuffix=" ms"
              height={240}
              referenceLines={[
                { value: 2000, label: "SLO 2000 ms", tone: "danger" },
              ]}
            />
            <Text tone="tertiary" size="small">
              Y-axis: milliseconds. Source: metrics.json case_latency_ms.
            </Text>
          </Stack>
          <Stack gap={8}>
            <H3>Max case latency (ms)</H3>
            <BarChart
              categories={["A1", "A2", "A3"]}
              series={[
                {
                  name: "Max case (ms)",
                  data: [164.2, 2561.0, 3346.7],
                  tone: "danger",
                },
              ]}
              valueSuffix=" ms"
              height={240}
              referenceLines={[{ value: 2000, label: "SLO 2000 ms" }]}
            />
            <Text tone="tertiary" size="small">
              Y-axis: milliseconds. Full-run wall: A1 2.1s, A2 75.6s, A3
              64.0s.
            </Text>
          </Stack>
        </Grid>
        <Table
          headers={[
            "Approach",
            "p50",
            "p95",
            "Max",
            "Wall (100 cases)",
            "SLO good-case rate",
            "Bad cases (allowed 5)",
          ]}
          columnAlign={["left", "right", "right", "right", "right", "right", "right"]}
          rows={[
            ["A1 rules", "11 ms", "67 ms", "164 ms", "2.1 s", "100%", "0"],
            ["A2", "1091 ms", "1743 ms", "2561 ms", "75.6 s", "98%", "2"],
            ["A3", "115 ms", "1633 ms", "3347 ms", "64.0 s", "99%", "1"],
          ]}
        />
      </Stack>

      <Divider />

      <Stack gap={12}>
        <H2>Five comparison rates</H2>
        <Text>
          ADR-41 bake-off rates. Wrong-label is 1 − class match. Auto-routed
          but wrong is ready_for_review with the wrong class. Manual-review is
          the share routed to an analyst. USD/case is list price. Latency is
          case-span p95.
        </Text>
        <Table
          headers={[
            "Approach",
            "Wrong-label",
            "Auto-routed but wrong",
            "Manual-review",
            "USD / case",
            "Latency p95",
          ]}
          columnAlign={["left", "right", "right", "right", "right", "right"]}
          rows={[
            ["A1 rules", "41%", "2 / 24 ready", "74%", "$0", "67 ms"],
            ["A2", "3%", "2 / 34 ready", "45%", "$0.000046", "1743 ms"],
            ["A3", "4%", "2 / 33 ready", "46%", "$0.000054", "1633 ms"],
          ]}
        />
        <Text tone="secondary" size="small">
          The two auto-routed-wrong cases on A2 and A3 are CARD-S054 (gold
          unsupported, predicted unauthorized, routed ready) and CARD-S060
          (gold ambiguous, predicted duplicate_charge, routed ready). CARD-S024
          is wrong but manual_review, so it is not in that rate.
        </Text>
      </Stack>

      <Divider />

      <Stack gap={12}>
        <H2>Score vs gold</H2>
        <BarChart
          categories={CHECKS.map((c) => c.replaceAll("_", " "))}
          series={APPROACHES.map((a, i) => ({
            name: `A${a.id}`,
            data: a.checks,
            tone: (["neutral", "info", "success"] as const)[i],
          }))}
          height={280}
        />
        <Text tone="tertiary" size="small">
          Y-axis: cases passing each eval check out of 100. Source: eval.json
          from the local a1-now, a2-now, and a3-now runs. Those files are
          gitignored under reports/synthetic.
        </Text>
      </Stack>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H2>Recall by gold class</H2>
          <BarChart
            categories={[
              "unauthorized",
              "duplicate",
              "service_not_received",
              "ambiguous",
              "unsupported",
            ]}
            series={[
              { name: "A1", data: [69.6, 57.1, 5.3, 96.6, 40.0], tone: "neutral" },
              { name: "A2", data: [100, 100, 100, 96.6, 86.7], tone: "info" },
              { name: "A3", data: [100, 100, 94.7, 96.6, 86.7], tone: "success" },
            ]}
            valueSuffix="%"
            height={240}
          />
          <Text tone="tertiary" size="small">
            Y-axis: recall %. Gold mix: unauthorized 23, duplicate 14, SNR 19,
            ambiguous 29, unsupported 15.
          </Text>
        </Stack>
        <Stack gap={8}>
          <H2>Where the 100 cases went</H2>
          <Grid columns={3} gap={8}>
            {APPROACHES.map((a) => (
              <Stack key={a.id} gap={6}>
                <H3>A{a.id}</H3>
                <PieChart
                  donut
                  size={140}
                  data={[
                    {
                      label: "manual",
                      value: a.routes.manual_review,
                      tone: "warning",
                    },
                    {
                      label: "ready",
                      value: a.routes.ready_for_review,
                      tone: "success",
                    },
                    {
                      label: "request info",
                      value: a.routes.request_information,
                      tone: "info",
                    },
                  ]}
                />
              </Stack>
            ))}
          </Grid>
        </Stack>
      </Grid>

      <Stack gap={8}>
        <H2>Confusion vs gold</H2>
        <Grid columns={3} gap={12}>
          <Stack gap={6}>
            <H3>A1 — 59 correct</H3>
            <Table
              headers={["gold \\ pred", "unauth", "dup", "snr", "amb", "unsup"]}
              columnAlign={["left", "right", "right", "right", "right", "right"]}
              rows={[
                ["unauthorized 23", "16", "0", "0", "7", "0"],
                ["duplicate 14", "0", "8", "0", "6", "0"],
                ["SNR 19", "0", "0", "1", "18", "0"],
                ["ambiguous 29", "0", "1", "0", "28", "0"],
                ["unsupported 15", "1", "0", "0", "8", "6"],
              ]}
            />
          </Stack>
          <Stack gap={6}>
            <H3>A2 — 97 correct</H3>
            <Table
              headers={["gold \\ pred", "unauth", "dup", "snr", "amb", "unsup"]}
              columnAlign={["left", "right", "right", "right", "right", "right"]}
              rows={[
                ["unauthorized 23", "23", "0", "0", "0", "0"],
                ["duplicate 14", "0", "14", "0", "0", "0"],
                ["SNR 19", "0", "0", "19", "0", "0"],
                ["ambiguous 29", "0", "1", "0", "28", "0"],
                ["unsupported 15", "1", "0", "0", "1", "13"],
              ]}
            />
          </Stack>
          <Stack gap={6}>
            <H3>A3 — 96 correct</H3>
            <Table
              headers={["gold \\ pred", "unauth", "dup", "snr", "amb", "unsup"]}
              columnAlign={["left", "right", "right", "right", "right", "right"]}
              rows={[
                ["unauthorized 23", "23", "0", "0", "0", "0"],
                ["duplicate 14", "0", "14", "0", "0", "0"],
                ["SNR 19", "0", "0", "18", "1", "0"],
                ["ambiguous 29", "0", "1", "0", "28", "0"],
                ["unsupported 15", "1", "0", "0", "1", "13"],
              ]}
            />
          </Stack>
        </Grid>
      </Stack>

      <Stack gap={8}>
        <H2>Classifier path (correct / n)</H2>
        <Grid columns={3} gap={12}>
          {APPROACHES.map((a) => (
            <Table
              key={a.id}
              headers={[`A${a.id} path`, "ok / n"]}
              rows={a.paths.map(([path, ok, n]) => [
                String(path),
                `${ok} / ${n}`,
              ])}
            />
          ))}
        </Grid>
        <Text tone="secondary" size="small">
          Pairwise agreement: A1 vs A2 62, A1 vs A3 63, A2 vs A3 99. The only
          A2/A3 split is CARD-S078 (A2 SNR, A3 ambiguous; gold SNR).
        </Text>
      </Stack>

      <Stack gap={8}>
        <H2>Remaining class misses</H2>
        <Table
          headers={["Case", "Gold", "A2", "A3", "Why it still misses"]}
          rows={[
            [
              "CARD-S024",
              "unsupported",
              "ambiguous",
              "ambiguous",
              "ATM paraphrase plus Authorization: denied. Rules dump to ambiguous; model never asked.",
            ],
            [
              "CARD-S054",
              "unsupported",
              "unauthorized",
              "unauthorized",
              "Same cash-plus-denied pattern; assigned unauthorized and ready_for_review.",
            ],
            [
              "CARD-S060",
              "ambiguous",
              "duplicate_charge",
              "duplicate_charge",
              "Intake looks like a duplicate; follow-up retracts it. Rules only read Authorization labels.",
            ],
            [
              "CARD-S078",
              "service_not_received",
              "service_not_received",
              "ambiguous",
              "Cancelled renewal. A2 hits. A3 Jev/model still unclear.",
            ],
          ]}
        />
      </Stack>

      <Stack gap={8}>
        <H2>Ops extras (same on all three runs)</H2>
        <Grid columns={4} gap={12}>
          <Stat value="1" label="Untrusted-instruction cases" />
          <Stat value="0" label="Circuit-breaker opens" />
          <Stat value="5" label="Tool-call exhausted" />
          <Stat value="200" label="Tool calls" />
        </Grid>
        <Text tone="secondary" size="small">
          Injection and tool-budget figures are corpus properties, not
          classifier properties.
        </Text>
      </Stack>
    </Stack>
  );
}
