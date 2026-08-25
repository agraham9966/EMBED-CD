# How it works

!!! warning "Not written yet"
    The sections below are the plan. Each answers one question; together they are the argument
    for why the tool is shaped the way it is, which only reads properly in order.

## What an embedding is

What the 64 numbers behind every 10 m pixel actually represent, and why summarising a whole
year is different from photographing a day.

## The change score

Why the distance between two years means "something changed here", why the scale is absolute
rather than stretched per scene, and what a cutoff of 0.15 actually means.

## Data coverage

Why "no data" is its own answer and never a blank — and why misreading it is the mistake that
makes a change map untrustworthy.

## Detail, resolution and cost {#detail-and-cost}

What Detail changes: the pixel size you get, the resolution the data is read at, and what an
area will cost you before you commit to it. Also why the output lands in a CRS where a metre
is a metre, which may not be your project's.

## Thresholds and objects {#thresholds-and-objects}

Why moving the cutoff is free, and what happens when you cut objects — including the part that
surprises people: objects are **not** nested across thresholds, so re-cutting gives different
polygons rather than a subset.

## Classifying {#classifying}

How an object gets the embedding of what it covers, what the classifier learns from a handful
of clicks, why it may answer *unknown*, and what Transition and End state modes each mean.

## Saving and areas {#saving-and-areas}

What persists and where, how a run reopens complete with its labels, and how several areas
coexist in one project.
