// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
// A small typed facade over the existing Foundation JSON parser, not another
// cache/metadata decoder or a new JSON dependency.
#import <Foundation/Foundation.h>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>
namespace PreparationJson {
class Value {
 __strong id object;
 explicit Value(id o,int):object(o?:[NSNull null]){}
 static NSString* String(const std::string&s){auto v=[[NSString alloc]initWithBytes:s.data() length:s.size() encoding:NSUTF8StringEncoding];if(!v)throw std::runtime_error("JSON UTF-8");return v;}
 static bool Boolean(id o){return o&&CFGetTypeID((__bridge CFTypeRef)o)==CFBooleanGetTypeID();}
public:
 Value():object([NSNull null]){}
 Value(bool b):object(@(b)){}
 template<typename T>requires(std::is_integral_v<T>&&!std::is_same_v<T,bool>)
 Value(T n):object(@(n)){}
 Value(const std::string&s):object(String(s)){}
 Value(const char*s):Value(std::string(s)){}
 Value(std::initializer_list<std::pair<std::string,Value>> fields):object([NSMutableDictionary new]){
  for(const auto& f:fields)[(NSMutableDictionary*)object setObject:f.second.object forKey:String(f.first)];
 }
 static Value parse(const std::vector<unsigned char>& b){
  auto data=[NSData dataWithBytes:b.data() length:b.size()];NSError*error=nil;
  id v=[NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
  if(![v isKindOfClass:[NSDictionary class]])throw std::runtime_error("JSON object required");return Value(v,0);
 }
 static Value array(){return Value([NSMutableArray new],0);}
 Value at(const std::string& key)const{
  if(![object isKindOfClass:[NSDictionary class]])throw std::runtime_error("JSON dictionary expected");
  id v=[object objectForKey:String(key)];if(!v)throw std::runtime_error("JSON missing key: "+key);return Value(v,0);
 }
 template<typename T>T get()const{
  if constexpr(std::is_same_v<T,std::string>){
   if(![object isKindOfClass:[NSString class]])throw std::runtime_error("JSON string expected");
   auto bytes=[object dataUsingEncoding:NSUTF8StringEncoding];return std::string(static_cast<const char*>(bytes.bytes),bytes.length);
  }else if constexpr(std::is_same_v<T,bool>){
   if(!Boolean(object))throw std::runtime_error("JSON boolean expected");return [object boolValue];
  }else{
   static_assert(std::is_integral_v<T>);
   if(![object isKindOfClass:[NSNumber class]]||Boolean(object)||CFNumberIsFloatType((__bridge CFNumberRef)object))throw std::runtime_error("JSON integer expected");
   if constexpr(std::is_unsigned_v<T>){if([object longLongValue]<0||[object unsignedLongLongValue]>std::numeric_limits<T>::max())throw std::runtime_error("JSON unsigned range");return [object unsignedLongLongValue];}
   else {auto n=[object longLongValue];if(n<std::numeric_limits<T>::min()||n>std::numeric_limits<T>::max())throw std::runtime_error("JSON signed range");return n;}
  }
 }
 operator std::string()const{return get<std::string>();}
 class Slot {
  __strong NSMutableDictionary* parent;std::string key;
 public:Slot(id p,std::string k):parent(p),key(std::move(k)){}
  void operator=(const Value& v){[parent setObject:v.object forKey:String(key)];}
 };
 Slot operator[](std::string k){if(![object isKindOfClass:[NSMutableDictionary class]])throw std::runtime_error("JSON writable dictionary expected");return Slot(object,std::move(k));}
 size_t size()const{if(![object isKindOfClass:[NSArray class]])throw std::runtime_error("JSON array expected");return [object count];}
 void push_back(const Value& v){if(![object isKindOfClass:[NSMutableArray class]])throw std::runtime_error("JSON writable array expected");[object addObject:v.object];}
 struct Iterator {
  __strong NSArray* array;size_t index;
  Value operator*()const{return Value([array objectAtIndex:index],0);}
  Iterator& operator++(){++index;return *this;}
  bool operator!=(const Iterator& other)const{return index!=other.index;}
 };
 Iterator begin()const{size();return {(NSArray*)object,0};}
 Iterator end()const{return {(NSArray*)object,size()};}
 std::string dump()const{
  NSError*error=nil;auto b=[NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingSortedKeys error:&error];
  if(!b)throw std::runtime_error("JSON output encoding");return std::string(static_cast<const char*>(b.bytes),b.length);
 }
 friend bool operator==(const Value&a,const Value&b){
  if(Boolean(a.object)!=Boolean(b.object))return false;
  if([a.object isKindOfClass:[NSNumber class]]&&[b.object isKindOfClass:[NSNumber class]]&&!Boolean(a.object)&&
     CFNumberIsFloatType((__bridge CFNumberRef)a.object)!=CFNumberIsFloatType((__bridge CFNumberRef)b.object))return false;
  return [a.object isEqual:b.object];
 }
 template<typename T>friend bool operator==(const Value&a,const T&b){return a==Value(b);}
};
}
