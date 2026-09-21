import Mathlib

theorem algebra_5055 (x y : ℤ) (hx : 0 < x) (hy : 0 < y)
    (h : x ^ 2 + x * y - 3 * x + 2 * y - 2016 = 0) :
    (x, y) = (15, 108) ∨ (x, y) = (32, 32) := by
  have h1 : (x + 2) * (x + y - 5) = 2006 := by
    nlinarith

  have h2 : (x + 2) ∣ 2006 := by
    use x + y - 5
    all_goals
      linarith

  have h5 : (x + 2) > 0 := by
    nlinarith

  have h6 : (x + 2) ≤ 2006 := by
    exact Int.le_of_dvd (show (0 : ℤ) < 2006 by norm_num) h2

  have h7 : (x + 2) = 1 ∨ (x + 2) = 2 ∨ (x + 2) = 17 ∨
      (x + 2) = 34 ∨ (x + 2) = 59 ∨ (x + 2) = 118 ∨
      (x + 2) = 1003 ∨ (x + 2) = 2006 := by
    interval_cases h8 : (x + 2) <;> norm_num at h2 h1 ⊢
    all_goals
      omega

  rcases h7 with (h7 | h7 | h7 | h7 | h7 | h7 | h7 | h7)

  · exfalso
    have h12 : x = -1 := by
      omega
    nlinarith

  · exfalso
    have h12 : x = 0 := by
      omega
    nlinarith

  · have h12 : x = 15 := by
      omega
    have h13 : y = 108 := by
      nlinarith [h1, h7]
    left
    simp [h12, h13]

  · have h12 : x = 32 := by
      omega
    have h13 : y = 32 := by
      nlinarith [h1, h7]
    right
    simp [h12, h13]

  · exfalso
    have h12 : x = 57 := by
      omega
    have h13 : y ≤ 0 := by
      nlinarith [h1, h7]
    nlinarith

  · exfalso
    have h12 : x = 116 := by
      omega
    have h13 : y ≤ 0 := by
      nlinarith [h1, h7]
    nlinarith

  · exfalso
    have h12 : x = 1001 := by
      omega
    have h13 : y ≤ 0 := by
      nlinarith [h1, h7]
    nlinarith

  · exfalso
    have h12 : x = 2004 := by
      omega
    have h13 : y ≤ 0 := by
      nlinarith [h1, h7]
    nlinarith
