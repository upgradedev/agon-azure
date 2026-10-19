/*
  Glossary for the match model terms the newsroom reads. Each definition
  follows docs/INTERPRET.md and src/agon/interpret in the repository, which
  compute these numbers from the synthetic event log alone.
*/
export type GlossaryId = "xg" | "xt" | "ppda" | "control" | "wp";

export interface GlossaryEntry {
  id: GlossaryId;
  term: string;
  short: string;
  definition: string;
  source: string;
}

export const GLOSSARY: Record<GlossaryId, GlossaryEntry> = {
  xg: {
    id: "xg",
    term: "xG",
    short: "expected goals",
    definition:
      "Expected goals: the modelled chance that a shot becomes a goal, from its distance and angle to goal, whether it came from inside the area, under pressure, as a header, a direct free kick or a penalty. A shot with xG 0.20 goes in about one time in five.",
    source: "src/agon/interpret/features.py, a logistic model fitted on synthetic training matches",
  },
  xt: {
    id: "xt",
    term: "xT",
    short: "expected threat",
    definition:
      "Expected threat: how likely possession in each zone of a 16 by 12 grid is to lead to a goal. A completed pass or carry adds the threat of the zone it ends in minus the zone it started in.",
    source: "src/agon/interpret/features.py, values fitted on synthetic training matches",
  },
  ppda: {
    id: "ppda",
    term: "PPDA",
    short: "passes per defensive action",
    definition:
      "Passes allowed per defensive action: the opponent's passes in their own 60 percent of the pitch, divided by the side's tackles, fouls and interceptions in that area. Lower means a more intense press.",
    source: "src/agon/interpret/metrics.py, per match and per 15-minute block",
  },
  control: {
    id: "control",
    term: "Control vs chaos",
    short: "how settled the play is",
    definition:
      "For each 5-minute window, the mean of two parts: how evenly the on-ball actions spread across possession chains, and how many chains end in an open-play turnover. Low reads as control, where one side keeps the ball; high reads as chaos.",
    source: "src/agon/interpret/metrics.py, banded by terciles frozen from training matches",
  },
  wp: {
    id: "wp",
    term: "Win probability",
    short: "our model's chance of each result",
    definition:
      "The chance of a home win, a draw and an away win from the current score, the time left and the players on the pitch, treating each side's remaining goals as a Poisson count. It is our own model, and it does not model a trailing side pressing late.",
    source: "src/agon/interpret/wp.py, fitted on synthetic training matches",
  },
};

export const GLOSSARY_ORDER: GlossaryId[] = ["xg", "xt", "ppda", "control", "wp"];
