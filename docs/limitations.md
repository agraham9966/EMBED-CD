# Limitations

What this tool cannot tell you, and where it is likely to be wrong. 

## There are no accuracy numbers

We have no labelled ground truth, so we do not publish an accuracy, and neither should you
infer one. Every figure in these docs is an **engineering** measurement — tile counts, timings,
file sizes, coverage extents — all real and re-runnable, none of them accuracy.

If you need real numbers for your area, validate against an independent dated source. For
forestry in British Columbia the obvious candidate is the province's published harvest polygons.

**For this reason**, we provide Sentinel-2 imagery basemaps so you can independently validate the quality of 
your outputs. Have fun and share any interesting findings! 

## A high change score is not proof the land changed

The embedding summarises a whole year, climate and phenology included. A drought, a late spring
or an unusual snow year moves the embedding without anything on the ground being cut, built or
burned. As Burns (2026) puts it, large interannual differences can arise even when the land
surface itself has not changed ([Google Earth blog][burns]).

The score cannot separate those causes. A cutoff finds *deviation*, and deciding which
deviations are land-surface change is what the labelling step is for.

## Annual only — and late-year change is muted

An embedding is a summary over a fixed period, and the published product's period is the
calendar year ([AlphaEarth Foundations][aef]). Two consequences:

- **Nothing dates a change more precisely than the year it happened.**
- **A change late in year B reads weaker than it should.** Clear a block in October and the
  year-B embedding still summarises nine months of standing forest, so the score is diluted and
  the object may fall below your cutoff. If you suspect a late-year change, compare against the
  *following* year instead.

The same averaging absorbs short-lived events. Floods, and fire scars that green up within the
year, are poorly served; permanent change is what this suits.


## The first class you label accepts the opposite change

With one class there is nothing to discriminate against, so objects are ranked by similarity to
the average of your examples — and that average is dominated by what the place *was*. Label
three cleared blocks and regrowth from the same forest baseline scores just as well.

On a synthetic fixture — planted clusters, not real ground — **100% of the reverse change
was accepted**, identically in all three feature modes.
It is the first state every session is in. Label a second, contrasting class as early as you
can — that is what switches the head to logistic regression.

## Classes do not travel well

The model receives no coordinate information, which its authors speculate is why it needs far
more examples to learn climate gradients ([AlphaEarth Foundations][aef]). Expect a classifier
trained in one climatic zone to (possibly) degrade in another. Sometimes it works pretty well though! 


## Coverage is not truly global

Nothing exists beyond ±83.36°, nothing over open ocean, and coverage **grew** between 2017 and
2025 — so an early year paired with a recent one covers less ground than either does alone. See
[Data coverage](how-it-works.md#data-coverage) for the map and the numbers.

## The dimensions mean nothing on their own

The 64 numbers are not bands and have no individual interpretation. You cannot inspect *why*
two years differ, only that they do, and by how much. Interpretability and independent
validation of these embeddings are both open problems.

## The reference photos are composites

The before/after imagery is cloud-free annual compositing, not a dated acquisition. It answers
"what was here that year" and never "what date did this change". It is also **non-commercial**
— see [Reference](reference.md#licences-and-attribution).

[aef]: https://arxiv.org/abs/2507.22291
[burns]: https://medium.com/google-earth/rethinking-change-detection-and-attribution-how-you-compare-satellite-embeddings-matters-858f17f577d7
