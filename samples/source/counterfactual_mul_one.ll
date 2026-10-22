define i32 @identity(i32 %arg) {
entry:
  %value = mul i32 %arg, 1
  ret i32 %value
}

define i32 @main() {
entry:
  %result = call i32 @identity(i32 40)
  ret i32 0
}
