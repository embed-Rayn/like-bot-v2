# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`like-bot-v2` is an upgrade/rewrite of an existing legacy program with this behavior:

1. Takes a search **주제(topic)** and **날짜(date)** as input.
2. Searches the Naver Blog search box with those criteria.
3. Opens the blog(s) returned by the search.
4. Likes ("좋아요") the 3 most recent posts on each blog.
5. Exits.

**Purpose**: this is a targeted outreach/relationship-building tool — it visits blogs matching a topic/date niche and engages (likes) with their recent posts, rather than liking at random. When making design or behavior decisions, keep this targeting intent intact (e.g. topic/date filtering exists to find *relevant* bloggers, not just any blog).

## Current Status

This repository is currently empty (no commits, no source files, no remote history). The legacy implementation has not been migrated in yet — it will be brought in and reviewed together with the user before/while the upgrade work starts. Nothing below about stack, commands, or architecture can be assumed until that legacy code is actually read.

**Before doing any implementation work here:**
- Ask for (or locate) the legacy program source if it isn't already present in the repo.
- Read it to confirm actual language, dependencies, and automation approach (e.g. Selenium/Playwright/requests-based scraping, or an official API) — do not assume.
- Once the legacy code lands, replace this section with real build/lint/test commands and an architecture overview (how the search step, blog navigation, and "like" action are structured), per the standard CLAUDE.md format.

## Domain Notes

- The core flow to preserve (or deliberately change, with the user's sign-off) during the upgrade is: topic+date search → blog result → top 3 most recent posts → like each.
- Confirm with the user whether the upgrade changes the automation method itself (e.g. browser automation vs. API) or just modernizes the existing approach — this materially changes the architecture.
