define i32 @identity(i32 %arg) {
entry:
  %value = add i32 %arg, 0
  ret i32 %value
}

define i32 @main() {
entry:
  %result = call i32 @identity(i32 40)
  ret i32 0
}